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


def text_block(text):
    return SimpleNamespace(type="text", text=text)


class FakeStream:
    def __init__(self, chunks, stop_reason="end_turn", error=None):
        self.chunks = chunks
        self.stop_reason = stop_reason
        self.error = error

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    @property
    def text_stream(self):
        for chunk in self.chunks:
            yield chunk
        if self.error:
            raise self.error

    def get_final_message(self):
        content = [text_block("".join(self.chunks))]
        return SimpleNamespace(stop_reason=self.stop_reason, content=content)


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


class FridayPersonaTest(unittest.TestCase):
    FRIDAY_EVENING = datetime.datetime(2026, 11, 6, 19, 30)  # 2026-11-06 是星期五

    def make(self, call_me="老板"):
        return app.friday_persona(call_me, clock=lambda: self.FRIDAY_EVENING)

    def test_greeting_uses_time_and_name(self):
        persona = self.make("队长")
        self.assertEqual(persona.name, "星期五")
        self.assertIn("晚上好,队长", persona.greeting)
        self.assertIn("2026-11-06 星期五 19:30", persona.greeting)
        self.assertIn("队长", persona.farewell)

    def test_system_prompt_has_name_and_current_time(self):
        system = self.make("队长").system()
        self.assertIn("“队长”", system)
        self.assertIn("当前本地时间:2026-11-06 星期五 19:30", system)

    def test_system_prompt_is_recomputed_each_request(self):
        times = iter([
            datetime.datetime(2026, 11, 6, 9, 0),   # 创建人设时
            datetime.datetime(2026, 11, 6, 9, 0),   # 第一次提问
            datetime.datetime(2026, 11, 7, 10, 0),  # 第二次提问,已经过了一天
        ])
        persona = app.friday_persona(clock=lambda: next(times))
        client = FakeClient(FakeStream(["A1"]), FakeStream(["A2"]))
        bot = app.Assistant(client=client, system=persona.system)

        bot.ask("Q1", lambda t: None)
        bot.ask("Q2", lambda t: None)

        self.assertIn("2026-11-06 星期五 09:00", client.calls[0]["system"])
        self.assertIn("2026-11-07 星期六 10:00", client.calls[1]["system"])

    def test_time_greeting(self):
        cases = {5: "早上好", 11: "早上好", 12: "下午好", 18: "晚上好", 23: "夜深了", 2: "夜深了"}
        for hour, expected in cases.items():
            self.assertEqual(app.time_greeting(datetime.datetime(2026, 1, 1, hour)), expected)

    def test_default_assistant_keeps_plain_system_prompt(self):
        client = FakeClient(FakeStream(["ok"]))
        app.Assistant(client=client).ask("hi", lambda t: None)
        self.assertEqual(client.calls[0]["system"], app.SYSTEM_PROMPT)

    def test_main_selects_friday_persona(self):
        env = {"ANTHROPIC_API_KEY": "sk-test", "FRIDAY_CALL_ME": "队长"}
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(app, "Assistant") as assistant_cls, \
                mock.patch.object(app, "chat") as chat:
            app.main(["--persona", "friday", "--env-file", os.devnull])

        persona = chat.call_args.args[1]
        self.assertEqual(persona.name, "星期五")
        self.assertIn("队长", persona.greeting)
        self.assertIs(assistant_cls.call_args.kwargs["system"], persona.system)

    def test_call_me_argument_overrides_env(self):
        env = {"ANTHROPIC_API_KEY": "sk-test", "FRIDAY_CALL_ME": "队长"}
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(app, "Assistant"), \
                mock.patch.object(app, "chat") as chat:
            app.main(["--persona", "friday", "--call-me", "主人", "--env-file", os.devnull])
        self.assertIn("主人", chat.call_args.args[1].greeting)


class ChatLoopTest(unittest.TestCase):
    def run_chat(self, inputs, bot):
        with mock.patch("builtins.input", side_effect=inputs), mock.patch("builtins.print"):
            app.chat(bot)

    def test_clear_and_exit_commands(self):
        bot = app.Assistant(client=FakeClient(FakeStream(["A"])))
        self.run_chat(["Q", "/clear", "/exit"], bot)
        self.assertEqual(bot.messages, [])

    def test_friday_label_and_farewell(self):
        persona = app.friday_persona("队长")
        bot = app.Assistant(client=FakeClient(FakeStream(["收到"])), system=persona.system)
        with mock.patch("builtins.input", side_effect=["Q", "/exit"]), \
                mock.patch("builtins.print") as fake_print:
            app.chat(bot, persona)
        printed = [c.args[0] for c in fake_print.call_args_list if c.args]
        self.assertIn("\n星期五:", printed)
        self.assertEqual(printed[-1], persona.farewell)

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


if __name__ == "__main__":
    unittest.main()
