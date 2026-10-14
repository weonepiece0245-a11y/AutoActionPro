"""assistant.py 的单元测试,用假客户端代替真实 API,不访问网络。

运行:python -m unittest assistant/test_assistant.py
"""

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


class ChatLoopTest(unittest.TestCase):
    def run_chat(self, inputs, bot):
        with mock.patch("builtins.input", side_effect=inputs), mock.patch("builtins.print"):
            app.chat(bot)

    def test_clear_and_exit_commands(self):
        bot = app.Assistant(client=FakeClient(FakeStream(["A"])))
        self.run_chat(["Q", "/clear", "/exit"], bot)
        self.assertEqual(bot.messages, [])

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

    def test_default_path_is_next_to_script(self):
        self.assertEqual(
            app.DEFAULT_ENV_FILE,
            os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"),
        )


if __name__ == "__main__":
    unittest.main()
