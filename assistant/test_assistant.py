"""assistant.py 的单元测试,用假客户端代替真实 API,不访问网络。

运行:python -m unittest assistant/test_assistant.py
"""

import datetime
import importlib.util
import os
import sys
import tempfile
import types
import unittest
from types import SimpleNamespace
from unittest import mock

# 未安装 anthropic 时用桩模块代替,测试只依赖其中的异常类型
if "anthropic" not in sys.modules:
    try:
        import anthropic  # noqa: F401
    except ImportError:
        stub = types.ModuleType("anthropic")

        class AnthropicError(Exception):
            pass

        class APIConnectionError(AnthropicError):
            pass

        class APIStatusError(AnthropicError):
            def __init__(self, message="", status_code=500):
                super().__init__(message)
                self.message = message
                self.status_code = status_code

        class AuthenticationError(APIStatusError):
            pass

        class RateLimitError(APIStatusError):
            pass

        stub.AnthropicError = AnthropicError
        stub.APIConnectionError = APIConnectionError
        stub.APIStatusError = APIStatusError
        stub.AuthenticationError = AuthenticationError
        stub.RateLimitError = RateLimitError
        stub.Anthropic = lambda: None
        sys.modules["anthropic"] = stub

import anthropic  # noqa: E402

# 按文件路径加载,避免从仓库根目录运行时 "assistant" 被解析成目录
_spec = importlib.util.spec_from_file_location(
    "assistant_app", os.path.join(os.path.dirname(os.path.abspath(__file__)), "assistant.py")
)
app = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = app  # dataclass 需要能在 sys.modules 里找到所在模块
_spec.loader.exec_module(app)


tools = sys.modules["friday_tools"]  # assistant.py 加载时已导入
import voice  # noqa: E402  assistant.py 已把所在目录加入 sys.path


def text_block(text):
    return SimpleNamespace(type="text", text=text)


def tool_use(name, tool_input, id="toolu_1"):
    return SimpleNamespace(type="tool_use", name=name, input=tool_input, id=id)


class FakeStream:
    """模拟 SDK 的 MessageStream:迭代时产生事件,结束后给出完整消息。"""

    def __init__(self, chunks, stop_reason="end_turn", error=None, blocks=()):
        self.chunks = chunks
        self.stop_reason = stop_reason
        self.error = error
        self.blocks = list(blocks)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        for chunk in self.chunks:
            yield SimpleNamespace(type="text", text=chunk)
        for block in self.blocks:
            yield SimpleNamespace(type="content_block_stop", content_block=block)
        if self.error:
            raise self.error

    def get_final_message(self):
        content = [text_block("".join(self.chunks))] if self.chunks else []
        return SimpleNamespace(stop_reason=self.stop_reason, content=content + self.blocks)


class FakeClient:
    """记录每次请求的参数,并按顺序返回预设的 FakeStream。"""

    def __init__(self, *streams):
        self.streams = list(streams)
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))

    def _stream(self, **kwargs):
        # 复制一份历史,避免之后的修改影响断言
        kwargs["messages"] = list(kwargs["messages"])
        self.calls.append(kwargs)
        return self.streams.pop(0)


class AssistantTest(unittest.TestCase):
    def test_streams_text_in_order(self):
        client = FakeClient(FakeStream(["你好", ",", "世界"]))
        bot = app.Assistant(client=client)
        received = []

        answer = bot.ask("打个招呼", received.append)

        self.assertEqual(received, ["你好", ",", "世界"])
        self.assertEqual(answer, "你好,世界")

    def test_request_parameters(self):
        client = FakeClient(FakeStream(["ok"]))
        app.Assistant(client=client, model="m", effort="high").ask("hi", lambda t: None)

        call = client.calls[0]
        self.assertEqual(call["model"], "m")
        self.assertEqual(call["output_config"], {"effort": "high"})
        self.assertEqual(call["fallbacks"], "default")
        self.assertEqual(call["betas"], ["server-side-fallback-2026-07-01"])

    def test_history_alternates_across_turns(self):
        client = FakeClient(FakeStream(["A1"]), FakeStream(["A2"]))
        bot = app.Assistant(client=client)

        bot.ask("Q1", lambda t: None)
        bot.ask("Q2", lambda t: None)

        self.assertEqual([m["role"] for m in bot.messages], ["user", "assistant"] * 2)
        # 第二次请求带上了第一轮的完整上下文
        self.assertEqual([m["role"] for m in client.calls[1]["messages"]], ["user", "assistant", "user"])

    def test_error_rolls_back_question(self):
        client = FakeClient(FakeStream(["半句"], error=anthropic.APIConnectionError("断网")))
        bot = app.Assistant(client=client)

        with self.assertRaises(anthropic.APIConnectionError):
            bot.ask("Q", lambda t: None)
        self.assertEqual(bot.messages, [])

    def test_refusal_rolls_back_question(self):
        client = FakeClient(FakeStream([], stop_reason="refusal"))
        bot = app.Assistant(client=client)

        with self.assertRaises(app.RefusalError):
            bot.ask("Q", lambda t: None)
        self.assertEqual(bot.messages, [])

    def test_reset_clears_history(self):
        bot = app.Assistant(client=FakeClient(FakeStream(["A"])))
        bot.ask("Q", lambda t: None)
        bot.reset()
        self.assertEqual(bot.messages, [])


class TempDirTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

    def make_toolbox(self, approve=lambda action: True, facts=()):
        memory = tools.Memory(os.path.join(self.tmp, "memory.json"))
        for fact in facts:
            memory.add(fact)
        return tools.Toolbox(memory, approve, workdir=self.tmp)


class FridayPersonaTest(TempDirTest):
    FRIDAY_EVENING = datetime.datetime(2026, 11, 6, 19, 30)  # 2026-11-06 是星期五

    def make(self, call_me="老板", facts=()):
        toolbox = self.make_toolbox(facts=facts)
        return app.friday_persona(call_me, toolbox, clock=lambda: self.FRIDAY_EVENING)

    def test_greeting_uses_time_and_name(self):
        persona = self.make("队长")
        self.assertEqual(persona.name, "星期五")
        self.assertIn("晚上好,队长", persona.greeting)
        self.assertIn("2026-11-06 星期五 19:30", persona.greeting)
        self.assertIn("刚认识", persona.greeting)
        self.assertIn("队长", persona.farewell)

    def test_no_introduction_once_memories_exist(self):
        self.assertNotIn("刚认识", self.make(facts=["用户喜欢喝美式"]).greeting)

    def test_system_prompt_has_name_memories_and_computer(self):
        system = self.make("队长", facts=["用户喜欢喝美式"]).system()
        self.assertIn("称呼用户为“队长”", system)
        self.assertIn("Markdown 不会被渲染", system)
        self.assertIn("- 用户喜欢喝美式", system)
        self.assertIn(self.tmp, system)

    def test_system_is_frozen_and_time_goes_into_each_question(self):
        times = iter([
            datetime.datetime(2026, 11, 6, 9, 0),   # 创建人设时
            datetime.datetime(2026, 11, 6, 9, 0),   # 第一次提问
            datetime.datetime(2026, 11, 7, 10, 0),  # 第二次提问,已经过了一天
        ])
        persona = app.friday_persona(toolbox=self.make_toolbox(), clock=lambda: next(times))
        client = FakeClient(FakeStream(["A1"]), FakeStream(["A2"]))
        bot = app.Assistant(client=client, system=persona.system, context=persona.context)

        bot.ask("Q1", lambda t: None)
        bot.ask("Q2", lambda t: None)

        # 系统提示词整段对话不变,时间放在各自的用户消息里
        self.assertEqual(client.calls[0]["system"], client.calls[1]["system"])
        q1, q2 = bot.messages[0]["content"], bot.messages[2]["content"]
        self.assertIn("2026-11-06 星期五 09:00", q1[0]["text"])
        self.assertEqual(q1[1]["text"], "Q1")
        self.assertIn("2026-11-07 星期六 10:00", q2[0]["text"])

    def test_reset_rebuilds_system_with_new_memories(self):
        toolbox = self.make_toolbox()
        persona = app.friday_persona(toolbox=toolbox, clock=lambda: self.FRIDAY_EVENING)
        bot = app.Assistant(client=FakeClient(), system=persona.system, toolbox=toolbox)
        toolbox.memory.add("用户养了一只猫")
        self.assertNotIn("养了一只猫", bot.system)
        bot.reset()
        self.assertIn("养了一只猫", bot.system)

    def test_time_greeting(self):
        cases = {5: "早上好", 11: "早上好", 12: "下午好", 18: "晚上好", 23: "夜深了", 2: "夜深了"}
        for hour, expected in cases.items():
            self.assertEqual(app.time_greeting(datetime.datetime(2026, 1, 1, hour)), expected)

    def test_default_assistant_keeps_plain_prompt_and_no_tools(self):
        client = FakeClient(FakeStream(["ok"]))
        bot = app.Assistant(client=client)
        bot.ask("hi", lambda t: None)
        self.assertEqual(client.calls[0]["system"], app.SYSTEM_PROMPT)
        self.assertNotIn("tools", client.calls[0])
        self.assertEqual(bot.messages[0]["content"], "hi")

    def run_main(self, args, env):
        env = {"ANTHROPIC_API_KEY": "sk-test", "FRIDAY_MEMORY_FILE": os.path.join(self.tmp, "m.json"), **env}
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(app, "Assistant") as assistant_cls, \
                mock.patch.object(app, "chat") as chat:
            app.main(args + ["--env-file", os.devnull])
        return assistant_cls.call_args.kwargs, chat.call_args.args[1]

    def test_main_selects_friday_persona(self):
        kwargs, persona = self.run_main(["--persona", "friday"], {"FRIDAY_CALL_ME": "队长"})
        self.assertEqual(persona.name, "星期五")
        self.assertIn("队长", persona.greeting)
        self.assertIs(kwargs["system"], persona.system)
        self.assertIs(kwargs["toolbox"], persona.toolbox)
        self.assertEqual(kwargs["effort"], app.FRIDAY_EFFORT)
        self.assertEqual(str(persona.toolbox.memory.path), os.path.join(self.tmp, "m.json"))

    def test_main_options(self):
        kwargs, persona = self.run_main(
            ["--persona", "friday", "--call-me", "主人", "--effort", "high", "--no-web", "--workdir", self.tmp],
            {"FRIDAY_CALL_ME": "队长"},
        )
        self.assertIn("主人", persona.greeting)
        self.assertEqual(kwargs["effort"], "high")
        self.assertNotIn("web_search", [t["name"] for t in persona.toolbox.definitions()])
        self.assertEqual(str(persona.toolbox.workdir), self.tmp)

    def test_main_default_persona(self):
        kwargs, persona = self.run_main([], {})
        self.assertEqual(persona.name, "助理")
        self.assertIsNone(kwargs["toolbox"])
        self.assertEqual(kwargs["effort"], app.DEFAULT_EFFORT)


class ToolLoopTest(TempDirTest):
    def test_runs_tool_then_answers(self):
        toolbox = self.make_toolbox()
        client = FakeClient(
            FakeStream(["好的"], stop_reason="tool_use", blocks=[tool_use("remember", {"fact": "用户叫小明"})]),
            FakeStream(["记住了"]),
        )
        bot = app.Assistant(client=client, toolbox=toolbox)
        statuses = []

        answer = bot.ask("我叫小明", lambda t: None, statuses.append)

        self.assertEqual(answer, "好的记住了")
        self.assertEqual([f["text"] for f in toolbox.memory.facts], ["用户叫小明"])
        self.assertEqual([m["role"] for m in bot.messages], ["user", "assistant", "user", "assistant"])
        result = bot.messages[2]["content"][0]
        self.assertEqual(result["tool_use_id"], "toolu_1")
        self.assertNotIn("is_error", result)
        self.assertIn("记住了:用户叫小明", statuses)
        # 工具定义在每次请求里都一致,客户端工具开启了流式参数
        self.assertEqual(client.calls[0]["tools"], client.calls[1]["tools"])
        remember = next(t for t in client.calls[0]["tools"] if t["name"] == "remember")
        self.assertTrue(remember["eager_input_streaming"])

    def test_parallel_tool_results_go_in_one_message(self):
        toolbox = self.make_toolbox()
        calls = [tool_use("remember", {"fact": "A"}, id="t1"), tool_use("remember", {"fact": "B"}, id="t2")]
        client = FakeClient(FakeStream([], stop_reason="tool_use", blocks=calls), FakeStream(["ok"]))
        bot = app.Assistant(client=client, toolbox=toolbox)
        bot.ask("Q", lambda t: None)
        self.assertEqual([r["tool_use_id"] for r in bot.messages[2]["content"]], ["t1", "t2"])

    def test_pause_turn_is_resent_without_new_user_message(self):
        search = SimpleNamespace(type="server_tool_use", name="web_search", input={"query": "天气"}, id="s1")
        # 服务端筛选搜索结果时的 code_execution 不显示给用户
        code = SimpleNamespace(type="server_tool_use", name="code_execution", input={"code": "x"}, id="s2")
        client = FakeClient(FakeStream([], stop_reason="pause_turn", blocks=[code, search]), FakeStream(["晴"]))
        bot = app.Assistant(client=client, toolbox=self.make_toolbox())
        statuses = []
        self.assertEqual(bot.ask("天气?", lambda t: None, statuses.append), "晴")
        self.assertEqual([m["role"] for m in client.calls[1]["messages"]], ["user", "assistant"])
        self.assertEqual(statuses, ["搜索:天气"])

    def test_error_mid_loop_rolls_back_whole_turn(self):
        client = FakeClient(
            FakeStream([], stop_reason="tool_use", blocks=[tool_use("remember", {"fact": "A"})]),
            FakeStream([], error=anthropic.APIConnectionError("断网")),
        )
        bot = app.Assistant(client=client, toolbox=self.make_toolbox())
        with self.assertRaises(anthropic.APIConnectionError):
            bot.ask("Q", lambda t: None)
        self.assertEqual(bot.messages, [])

    def test_truncated_tool_input_is_not_run(self):
        approve = mock.Mock(return_value=True)
        call = tool_use("write_file", {"path": "a.txt", "content": "半"})
        client = FakeClient(FakeStream([], stop_reason="max_tokens", blocks=[call]))
        bot = app.Assistant(client=client, toolbox=self.make_toolbox(approve))
        with self.assertRaises(app.IncompleteAnswerError):
            bot.ask("Q", lambda t: None)
        approve.assert_not_called()
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "a.txt")))

    def test_step_limit(self):
        streams = [
            FakeStream([], stop_reason="tool_use", blocks=[tool_use("forget", {"keyword": "x"})])
            for _ in range(app.MAX_STEPS)
        ]
        bot = app.Assistant(client=FakeClient(*streams), toolbox=self.make_toolbox())
        with self.assertRaises(app.IncompleteAnswerError):
            bot.ask("Q", lambda t: None)
        self.assertEqual(bot.messages, [])


class MemoryTest(TempDirTest):
    def test_persists_between_sessions(self):
        path = os.path.join(self.tmp, "sub", "memory.json")
        memory = tools.Memory(path)
        memory.add("  用户  喜欢   爬山 ", today=datetime.date(2026, 11, 6))
        memory.add("用户 喜欢 爬山")  # 重复的不再记

        again = tools.Memory(path)
        self.assertEqual(again.facts, [{"text": "用户 喜欢 爬山", "added": "2026-11-06"}])
        self.assertIn("- 用户 喜欢 爬山(记于 2026-11-06)", again.as_prompt())

    def test_forget_by_keyword(self):
        memory = tools.Memory(os.path.join(self.tmp, "m.json"))
        for fact in ("喜欢咖啡", "讨厌咖啡因", "住在杭州"):
            memory.add(fact)
        self.assertEqual(memory.forget("咖啡"), ["喜欢咖啡", "讨厌咖啡因"])
        self.assertEqual(memory.forget("不存在"), [])
        self.assertEqual([f["text"] for f in tools.Memory(memory.path).facts], ["住在杭州"])

    def test_rejects_empty_and_too_long(self):
        memory = tools.Memory(os.path.join(self.tmp, "m.json"))
        with self.assertRaises(ValueError):
            memory.add("   ")
        with self.assertRaises(ValueError):
            memory.add("长" * (tools.MAX_FACT_CHARS + 1))

    def test_broken_file_is_backed_up(self):
        path = os.path.join(self.tmp, "m.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write("{坏掉的")
        self.assertEqual(tools.Memory(path).facts, [])
        self.assertTrue(os.path.exists(path + ".broken"))


class ToolboxTest(TempDirTest):
    def run_tool(self, toolbox, name, tool_input):
        statuses = []
        result = toolbox.run(tool_use(name, tool_input), statuses.append)
        return result, statuses

    def test_run_command_needs_approval(self):
        approve = mock.Mock(return_value=False)
        result, _ = self.run_tool(self.make_toolbox(approve), "run_command", {"command": "echo hi", "reason": "测试"})
        self.assertTrue(result["is_error"])
        self.assertIn("没有允许", result["content"])
        self.assertIn("echo hi", approve.call_args.args[0])

    def test_run_command_returns_output(self):
        result, statuses = self.run_tool(self.make_toolbox(), "run_command", {"command": "echo 你好", "reason": "测试"})
        self.assertNotIn("is_error", result)
        self.assertIn("退出码:0", result["content"])
        self.assertIn("你好", result["content"])
        self.assertEqual(statuses, ["执行命令:echo 你好"])

    def test_failed_command_is_error(self):
        result, _ = self.run_tool(self.make_toolbox(), "run_command", {"command": "exit 3", "reason": "测试"})
        self.assertTrue(result["is_error"])
        self.assertIn("退出码:3", result["content"])

    def test_write_then_read_file(self):
        toolbox = self.make_toolbox()
        result, _ = self.run_tool(toolbox, "write_file", {"path": "notes/待办.txt", "content": "买牛奶"})
        self.assertNotIn("is_error", result)
        result, _ = self.run_tool(toolbox, "read_file", {"path": "notes/待办.txt"})
        self.assertEqual(result["content"], "买牛奶")

    def test_write_file_declined(self):
        result, _ = self.run_tool(self.make_toolbox(lambda a: False), "write_file", {"path": "a.txt", "content": "x"})
        self.assertTrue(result["is_error"])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "a.txt")))

    def test_missing_file_is_reported_as_error(self):
        result, _ = self.run_tool(self.make_toolbox(), "read_file", {"path": "没有这个文件.txt"})
        self.assertTrue(result["is_error"])
        self.assertIn("FileNotFoundError", result["content"])

    def test_invalid_input_is_not_run(self):
        approve = mock.Mock(return_value=True)
        result, _ = self.run_tool(self.make_toolbox(approve), "run_command", {"command": 123})
        self.assertTrue(result["is_error"])
        self.assertIn("INVALID_JSON", result["content"])
        approve.assert_not_called()

    def test_unknown_tool(self):
        result, _ = self.run_tool(self.make_toolbox(), "fly", {})
        self.assertTrue(result["is_error"])

    def test_definitions(self):
        names = [t["name"] for t in self.make_toolbox().definitions()]
        self.assertEqual(
            names, ["web_search", "web_fetch", "remember", "forget", "run_command", "read_file", "write_file"]
        )


class ConsoleApproverTest(unittest.TestCase):
    def test_answers(self):
        approver = app.ConsoleApprover()
        with mock.patch("builtins.input", side_effect=["?", "y", "n", "a"]), mock.patch("builtins.print"):
            self.assertTrue(approver("执行命令:dir"))
            self.assertFalse(approver("执行命令:dir"))
            self.assertTrue(approver("执行命令:dir"))
        # 选了 a 之后不再询问
        with mock.patch("builtins.input", side_effect=AssertionError("不应该再问")):
            self.assertTrue(approver("执行命令:dir"))


class ChatLoopTest(unittest.TestCase):
    def run_chat(self, inputs, bot):
        with mock.patch("builtins.input", side_effect=inputs), mock.patch("builtins.print"):
            app.chat(bot)

    def test_clear_and_exit_commands(self):
        bot = app.Assistant(client=FakeClient(FakeStream(["A"])))
        self.run_chat(["Q", "/clear", "/exit"], bot)
        self.assertEqual(bot.messages, [])

    def test_friday_label_memory_commands_and_farewell(self):
        with tempfile.TemporaryDirectory() as d:
            toolbox = tools.Toolbox(tools.Memory(os.path.join(d, "m.json")), lambda a: True, workdir=d)
            toolbox.memory.add("喜欢咖啡")
            toolbox.memory.add("住在杭州")
            persona = app.friday_persona("队长", toolbox)
            bot = app.Assistant(client=FakeClient(FakeStream(["收到"])), system=persona.system, toolbox=toolbox)
            inputs = ["Q", "/memory", "/forget 咖啡", "/forget", "/exit"]
            with mock.patch("builtins.input", side_effect=inputs), mock.patch("builtins.print") as fake_print:
                app.chat(bot, persona)
        printed = [c.args[0] for c in fake_print.call_args_list if c.args]
        self.assertIn("\n星期五:", printed)
        self.assertIn("  - 喜欢咖啡", printed)
        self.assertIn("已忘掉:喜欢咖啡", printed)
        self.assertEqual(printed[-1], persona.farewell)
        self.assertEqual([f["text"] for f in toolbox.memory.facts], ["住在杭州"])

    def test_plain_assistant_has_no_memory_commands(self):
        client = FakeClient(FakeStream(["A"]))
        bot = app.Assistant(client=client)
        self.run_chat(["/memory", "/exit"], bot)
        self.assertEqual(client.calls[0]["messages"][0]["content"], "/memory")

    def test_eof_exits(self):
        bot = app.Assistant(client=FakeClient())
        self.run_chat(EOFError(), bot)

    def test_connection_error_keeps_chatting(self):
        client = FakeClient(
            FakeStream([], error=anthropic.APIConnectionError("断网")),
            FakeStream(["好了"]),
        )
        bot = app.Assistant(client=client)
        self.run_chat(["Q1", "Q2", "/quit"], bot)
        self.assertEqual([m["role"] for m in bot.messages], ["user", "assistant"])
        self.assertEqual(bot.messages[0]["content"], "Q2")


class LoadEnvFileTest(unittest.TestCase):
    def load(self, content, encoding="utf-8"):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, ".env")
            with open(path, "w", encoding=encoding) as f:
                f.write(content)
            app.load_env_file(path)

    def test_parses_lines(self):
        content = (
            "# 注释\n"
            "\n"
            "ANTHROPIC_API_KEY=sk-test\n"
            "export A = 'single'\n"
            'B="double"\n'
            "C=a=b\n"
            "not a pair\n"
        )
        with mock.patch.dict(os.environ, {}, clear=True):
            self.load(content)
            self.assertEqual(os.environ["ANTHROPIC_API_KEY"], "sk-test")
            self.assertEqual(os.environ["A"], "single")
            self.assertEqual(os.environ["B"], "double")
            self.assertEqual(os.environ["C"], "a=b")
            self.assertEqual(len(os.environ), 4)

    def test_handles_bom(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.load("ANTHROPIC_API_KEY=sk-bom\n", encoding="utf-8-sig")
            self.assertEqual(os.environ["ANTHROPIC_API_KEY"], "sk-bom")

    def test_environment_wins(self):
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "from-env"}, clear=True):
            self.load("ANTHROPIC_API_KEY=from-file\n")
            self.assertEqual(os.environ["ANTHROPIC_API_KEY"], "from-env")

    def test_missing_file_is_ignored(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            app.load_env_file(os.path.join(tempfile.gettempdir(), "no-such-dir", ".env"))
            self.assertEqual(dict(os.environ), {})

    def test_placeholder_key_exits_with_hint(self):
        example = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env.example")
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch("builtins.print"):
            with self.assertRaises(SystemExit):
                app.main(["--env-file", example])

    def test_default_path_is_next_to_script(self):
        self.assertEqual(
            app.DEFAULT_ENV_FILE,
            os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"),
        )


class FakeVoice:
    def __init__(self, heard=()):
        self.heard = list(heard)
        self.spoken = []

    def listen(self, on_status):
        item = self.heard.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def speak(self, text):
        self.spoken.append(text)


class VoiceChatTest(unittest.TestCase):
    def run_chat(self, inputs, bot, fake_voice, persona=None):
        with mock.patch("builtins.input", side_effect=inputs), mock.patch("builtins.print") as fake_print:
            app.chat(bot, persona, fake_voice)
        return [c.args[0] for c in fake_print.call_args_list if c.args]

    def test_enter_to_speak_and_answer_is_read_aloud(self):
        client = FakeClient(FakeStream(["明天", "晴"]))
        bot = app.Assistant(client=client)
        fake_voice = FakeVoice(["明天天气怎么样"])

        printed = self.run_chat(["", "/exit"], bot, fake_voice)

        self.assertEqual(client.calls[0]["messages"][0]["content"], "明天天气怎么样")
        self.assertIn("你(语音):明天天气怎么样", printed)
        greeting = app.default_persona().greeting
        self.assertEqual(fake_voice.spoken, [greeting, "明天晴", "再见!"])

    def test_typing_still_works_in_voice_mode(self):
        client = FakeClient(FakeStream(["好"]))
        fake_voice = FakeVoice()
        self.run_chat(["打字的问题", "/exit"], app.Assistant(client=client), fake_voice)
        self.assertEqual(client.calls[0]["messages"][0]["content"], "打字的问题")

    def test_unheard_and_recognition_errors_keep_going(self):
        client = FakeClient(FakeStream(["好"]))
        fake_voice = FakeVoice([None, voice.VoiceUnavailable("连不上"), "你好"])
        printed = self.run_chat(["", "", "", "/exit"], app.Assistant(client=client), fake_voice)
        self.assertIn("  没听清,再说一次?", printed)
        self.assertTrue(any("连不上" in p for p in printed))
        self.assertEqual(client.calls[0]["messages"][0]["content"], "你好")

    def test_saying_goodbye_exits(self):
        client = FakeClient()
        self.run_chat([""], app.Assistant(client=client), FakeVoice(["再见。"]))
        self.assertEqual(client.calls, [])

    def test_typed_goodbye_is_a_normal_question(self):
        client = FakeClient(FakeStream(["拜"]))
        self.run_chat(["再见", "/exit"], app.Assistant(client=client), FakeVoice())
        self.assertEqual(len(client.calls), 1)

    def test_speech_failure_does_not_stop_chat(self):
        class BrokenVoice(FakeVoice):
            def speak(self, text):
                raise RuntimeError("引擎坏了")

        client = FakeClient(FakeStream(["A"]), FakeStream(["B"]))
        bot = app.Assistant(client=client)
        self.run_chat(["Q1", "Q2", "/exit"], bot, BrokenVoice())
        self.assertEqual(len(client.calls), 2)

    def test_friday_voice_prompt(self):
        with tempfile.TemporaryDirectory() as d:
            toolbox = tools.Toolbox(tools.Memory(os.path.join(d, "m.json")), lambda a: True, workdir=d)
            self.assertIn("## 语音对话", app.friday_persona("队长", toolbox, voice=True).system())
            self.assertNotIn("## 语音对话", app.friday_persona("队长", toolbox).system())

    def test_main_creates_voice(self):
        env = {"ANTHROPIC_API_KEY": "sk-test", "FRIDAY_STT": "vosk"}
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(app, "Assistant"), \
                mock.patch.object(app, "chat") as chat, \
                mock.patch.object(voice, "create_voice", return_value="VOICE") as create:
            app.main(["--voice", "--env-file", os.devnull])
        self.assertEqual(create.call_args.args[0], "vosk")
        self.assertEqual(chat.call_args.args[2], "VOICE")

    def test_main_exits_when_voice_unavailable(self):
        env = {"ANTHROPIC_API_KEY": "sk-test"}
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(app, "Assistant"), \
                mock.patch.object(app, "chat") as chat, \
                mock.patch("builtins.print"), \
                mock.patch.object(voice, "create_voice", side_effect=voice.VoiceUnavailable("缺依赖")):
            with self.assertRaises(SystemExit):
                app.main(["--voice", "--stt", "google", "--env-file", os.devnull])
        chat.assert_not_called()


class CleanForSpeechTest(unittest.TestCase):
    def test_strips_markdown(self):
        text = (
            "## 结论\n"
            "**明天晴**,适合 `出门`。\n\n"
            "- 第一点\n"
            "1. 第二点\n"
            "| 时间 | 温度 |\n|---|---|\n| 早 | 10 |\n"
            "详见 [天气网](https://example.com) 或 https://example.com/a\n"
            "```python\nprint(1)\n```"
        )
        self.assertEqual(
            voice.clean_for_speech(text),
            "结论\n明天晴,适合 出门。\n第一点\n第二点\n, 时间 , 温度 ,\n, 早 , 10 ,\n详见 天气网 或 (链接)\n(代码略)",
        )

    def test_empty(self):
        self.assertEqual(voice.clean_for_speech("**  **"), "")


class FakeSR(types.ModuleType):
    """假的 speech_recognition 模块。"""

    class WaitTimeoutError(Exception):
        pass

    class UnknownValueError(Exception):
        pass

    class RequestError(Exception):
        pass

    def __init__(self, results):
        super().__init__("speech_recognition")
        self.results = list(results)
        fake = self

        class AudioSource:
            pass

        class Recognizer:
            pause_threshold = 0.8

            def adjust_for_ambient_noise(self, source, duration=1):
                fake.calibrated_with = source.stream.read(source.CHUNK)

            def listen(self, source, timeout=None, phrase_time_limit=None):
                item = fake.results.pop(0)
                if isinstance(item, fake.WaitTimeoutError):
                    raise item
                return item  # 其他异常留到识别时抛出

            def recognize_google(self, audio, language=None):
                fake.language = language
                if isinstance(audio, Exception):
                    raise audio
                return audio

        self.AudioSource = AudioSource
        self.Recognizer = Recognizer


class FakeRawInputStream:
    """假的 sounddevice.RawInputStream,read 返回全零的音频。"""

    instances = []

    def __init__(self, samplerate, channels, dtype, blocksize):
        self.args = (samplerate, channels, dtype, blocksize)
        self.started = self.closed = False
        FakeRawInputStream.instances.append(self)

    def start(self):
        self.started = True

    def stop(self):
        pass

    def close(self):
        self.closed = True

    def read(self, frames):
        return bytearray(frames * 2), False


def fake_sounddevice(stream_cls=FakeRawInputStream):
    module = types.ModuleType("sounddevice")
    module.RawInputStream = stream_cls
    return module


class FakeEngine:
    def __init__(self, voices):
        self.props = {"voices": voices}
        self.said = []

    def getProperty(self, name):
        return self.props[name]

    def setProperty(self, name, value):
        self.props[name] = value

    def say(self, text):
        self.said.append(text)

    def runAndWait(self):
        pass

    def stop(self):
        pass


class VoiceModuleTest(unittest.TestCase):
    def fake_tts(self, voices):
        engine = FakeEngine(voices)
        module = types.ModuleType("pyttsx3")
        module.init = lambda: engine
        return module, engine

    def test_speaker_picks_chinese_voice_and_cleans_text(self):
        voices = [
            SimpleNamespace(id="EN-US_ZIRA", name="Microsoft Zira", languages=[]),
            SimpleNamespace(id="TTS_MS_ZH-CN_HUIHUI_11.0", name="Microsoft Huihui", languages=[]),
        ]
        module, engine = self.fake_tts(voices)
        with mock.patch.dict(sys.modules, {"pyttsx3": module}):
            speaker = voice.Speaker()
        self.assertTrue(speaker.has_chinese_voice)
        self.assertEqual(engine.props["voice"], "TTS_MS_ZH-CN_HUIHUI_11.0")
        speaker.speak("**你好**")
        speaker.speak("** **")  # 清理后为空,不朗读
        self.assertEqual(engine.said, ["你好"])

    def test_speaker_without_chinese_voice(self):
        module, engine = self.fake_tts([SimpleNamespace(id="en", name="Zira", languages=[b"\x05en_US"])])
        with mock.patch.dict(sys.modules, {"pyttsx3": module}):
            self.assertFalse(voice.Speaker().has_chinese_voice)
        self.assertNotIn("voice", engine.props)

    def test_missing_dependencies(self):
        with mock.patch.dict(sys.modules, {"pyttsx3": None, "speech_recognition": None}):
            with self.assertRaises(voice.VoiceUnavailable):
                voice.Speaker()
            with self.assertRaises(voice.VoiceUnavailable):
                voice.Listener()

    def test_listener_google(self):
        sr = FakeSR(["你好星期五", FakeSR.WaitTimeoutError(), FakeSR.UnknownValueError()])
        with mock.patch.dict(sys.modules, {"speech_recognition": sr, "sounddevice": fake_sounddevice()}):
            listener = voice.Listener("google", on_status=lambda t: None)
            self.assertEqual(listener.listen(lambda t: None), "你好星期五")
            self.assertEqual(sr.language, "zh-CN")
            self.assertIsNone(listener.listen(lambda t: None))  # 没开口
            self.assertIsNone(listener.listen(lambda t: None))  # 没听清

    def test_listener_network_error_suggests_vosk(self):
        sr = FakeSR([FakeSR.RequestError("连不上")])
        with mock.patch.dict(sys.modules, {"speech_recognition": sr, "sounddevice": fake_sounddevice()}):
            listener = voice.Listener("google", on_status=lambda t: None)
            with self.assertRaises(voice.VoiceUnavailable) as ctx:
                listener.listen(lambda t: None)
        self.assertIn("--stt vosk", str(ctx.exception))

    def test_microphone_records_16bit_mono_and_closes(self):
        FakeRawInputStream.instances.clear()
        sr = FakeSR([])
        with mock.patch.dict(sys.modules, {"speech_recognition": sr, "sounddevice": fake_sounddevice()}):
            listener = voice.Listener("google", on_status=lambda t: None)
        self.assertIsInstance(listener.microphone, sr.AudioSource)
        self.assertEqual(sr.calibrated_with, bytes(1024 * 2))  # 校准时读到了一块音频
        stream = FakeRawInputStream.instances[0]
        self.assertEqual(stream.args, (16000, 1, "int16", 1024))
        self.assertTrue(stream.started and stream.closed)
        self.assertIsNone(listener.microphone.stream)

    def test_microphone_unavailable(self):
        def broken(*a, **k):
            raise RuntimeError("Error querying device -1")

        modules = {"speech_recognition": FakeSR([]), "sounddevice": fake_sounddevice(broken)}
        with mock.patch.dict(sys.modules, modules):
            with self.assertRaises(voice.VoiceUnavailable) as ctx:
                voice.Listener("google", on_status=lambda t: None)
        self.assertIn("麦克风", str(ctx.exception))

    def test_missing_sounddevice(self):
        with mock.patch.dict(sys.modules, {"speech_recognition": FakeSR([]), "sounddevice": None}):
            with self.assertRaises(voice.VoiceUnavailable):
                voice.Listener("google", on_status=lambda t: None)

    def test_vosk_model_already_downloaded(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, voice.VOSK_MODEL_NAME))
            with mock.patch("urllib.request.urlretrieve") as download:
                path = voice.ensure_vosk_model(d, on_status=lambda t: None)
            download.assert_not_called()
            self.assertEqual(path, os.path.join(d, voice.VOSK_MODEL_NAME))


if __name__ == "__main__":
    unittest.main()
