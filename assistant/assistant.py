"""AI 小助理:在终端里和 Claude 多轮对话。

用法:
    export ANTHROPIC_API_KEY=你的Key
    python assistant/assistant.py
"""

from __future__ import annotations

import argparse
import sys
from typing import Callable

import anthropic

DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_EFFORT = "low"
MAX_TOKENS = 64000

SYSTEM_PROMPT = (
    "你是一个友好、耐心的 AI 小助理。"
    "请用提问者使用的语言回答,回答要准确、清晰、简洁;"
    "不确定的事情要直接说明,不要编造。"
)

HELP_TEXT = "命令:/clear 清空对话  /exit 退出"


class RefusalError(Exception):
    """模型拒绝回答本轮问题。"""


class Assistant:
    """保存对话历史,并以流式方式向 Claude 提问。"""

    def __init__(
        self,
        client: anthropic.Anthropic | None = None,
        model: str = DEFAULT_MODEL,
        effort: str = DEFAULT_EFFORT,
    ) -> None:
        self.client = client if client is not None else anthropic.Anthropic()
        self.model = model
        self.effort = effort
        self.messages: list[dict] = []

    def ask(self, question: str, on_text: Callable[[str], None]) -> str:
        """提问并把回答逐段交给 on_text,返回完整回答文本。

        出错或被拒绝时撤回本轮问题,保证历史仍是 user/assistant 交替。
        """
        self.messages.append({"role": "user", "content": question})
        try:
            with self.client.beta.messages.stream(
                model=self.model,
                max_tokens=MAX_TOKENS,
                system=SYSTEM_PROMPT,
                messages=self.messages,
                output_config={"effort": self.effort},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            ) as stream:
                for text in stream.text_stream:
                    on_text(text)
                final = stream.get_final_message()
        except BaseException:
            self.messages.pop()
            raise

        if final.stop_reason == "refusal":
            self.messages.pop()
            raise RefusalError()

        # 追加完整 content(含 thinking 块),多轮对话才能继续使用
        self.messages.append({"role": "assistant", "content": final.content})
        return "".join(b.text for b in final.content if b.type == "text")

    def reset(self) -> None:
        self.messages.clear()


def print_text(text: str) -> None:
    print(text, end="", flush=True)


def chat(assistant: Assistant) -> None:
    print("你好!我是你的 AI 小助理,有什么问题尽管问我。")
    print(HELP_TEXT)
    while True:
        try:
            question = input("\n你:").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见!")
            return

        if not question:
            continue
        if question in ("/exit", "/quit"):
            print("再见!")
            return
        if question == "/clear":
            assistant.reset()
            print("对话已清空。")
            continue

        print("\n助理:", end="", flush=True)
        try:
            assistant.ask(question, print_text)
            print()
        except KeyboardInterrupt:
            print("\n(已中断本次回答)")
        except RefusalError:
            print("\n抱歉,这个问题我无法回答,换个问题试试吧。")
        except anthropic.AuthenticationError:
            print("\nAPI Key 无效或未设置,请先设置环境变量 ANTHROPIC_API_KEY。")
            return
        except anthropic.RateLimitError:
            print("\n请求太频繁了,请稍等片刻再试。")
        except anthropic.APIStatusError as e:
            print(f"\n服务出错(HTTP {e.status_code}):{e.message}")
        except anthropic.APIConnectionError:
            print("\n网络连接失败,请检查网络后重试。")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="AI 小助理:在终端里和 Claude 对话")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"模型 ID(默认 {DEFAULT_MODEL})")
    parser.add_argument(
        "--effort",
        default=DEFAULT_EFFORT,
        choices=["low", "medium", "high", "xhigh", "max"],
        help=f"思考深度,越高越仔细但越慢(默认 {DEFAULT_EFFORT})",
    )
    args = parser.parse_args(argv)

    try:
        assistant = Assistant(model=args.model, effort=args.effort)
    except anthropic.AnthropicError:
        print("未找到 API 凭据,请先设置环境变量 ANTHROPIC_API_KEY。", file=sys.stderr)
        sys.exit(1)
    chat(assistant)


if __name__ == "__main__":
    main()
