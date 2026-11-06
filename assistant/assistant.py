"""AI 小助理:在终端里和 Claude 多轮对话。

用法:
    在 assistant/.env 里写入 ANTHROPIC_API_KEY=你的Key(或设置同名环境变量)
    python assistant/assistant.py                    # 普通小助理
    python assistant/assistant.py --persona friday   # 私人助手“星期五”
"""

from __future__ import annotations

import argparse
import datetime
import os
import sys
from dataclasses import dataclass
from typing import Callable, Union

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

DEFAULT_CALL_ME = "老板"
WEEKDAYS = "一二三四五六日"

FRIDAY_PROMPT = (
    "你是“星期五”,用户的私人 AI 助手。"
    "性格冷静、干练、可靠,偶尔带一点幽默,但从不啰嗦。"
    "称呼用户为“{call_me}”。"
    "回答时先给结论,再补充必要的细节;能列步骤就列步骤;"
    "发现风险、遗漏或更好的做法时要主动提醒,并给出下一步建议。"
    "请用提问者使用的语言回答;不确定的事情要直接说明,不要编造。"
)

# system 可以是固定字符串,也可以是每次提问时调用的函数(用于注入当前时间等)
SystemPrompt = Union[str, Callable[[], str]]


def format_now(now: datetime.datetime) -> str:
    return f"{now:%Y-%m-%d} 星期{WEEKDAYS[now.weekday()]} {now:%H:%M}"


def time_greeting(now: datetime.datetime) -> str:
    hour = now.hour
    if 5 <= hour < 12:
        return "早上好"
    if 12 <= hour < 18:
        return "下午好"
    if 18 <= hour < 23:
        return "晚上好"
    return "夜深了"


@dataclass(frozen=True)
class Persona:
    """助手的“人设”:显示名、开场白、告别语和系统提示词。"""

    name: str
    greeting: str
    farewell: str
    system: SystemPrompt


def default_persona() -> Persona:
    return Persona(
        name="助理",
        greeting="你好!我是你的 AI 小助理,有什么问题尽管问我。",
        farewell="再见!",
        system=SYSTEM_PROMPT,
    )


def friday_persona(
    call_me: str = DEFAULT_CALL_ME,
    clock: Callable[[], datetime.datetime] = datetime.datetime.now,
) -> Persona:
    def system() -> str:
        # 每轮都带上当前时间,问“今天几号”“还有几天周末”时才答得准
        return FRIDAY_PROMPT.format(call_me=call_me) + f"\n当前本地时间:{format_now(clock())}。"

    now = clock()
    return Persona(
        name="星期五",
        greeting=f"{time_greeting(now)},{call_me}。星期五已上线,随时待命。\n现在是 {format_now(now)}。",
        farewell=f"好的,{call_me}。星期五下线,有需要随时叫我。",
        system=system,
    )


PERSONA_CHOICES = ("assistant", "friday")

DEFAULT_ENV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
PLACEHOLDER_KEY = "在这里填你的Key"  # 与 .env.example 中的占位符一致
KEY_HINT ="请设置环境变量 ANTHROPIC_API_KEY,或在 assistant/.env 里写入 ANTHROPIC_API_KEY=你的Key。"


def load_env_file(path: str) -> None:
    """把 .env 文件里的 KEY=VALUE 读进环境变量;已设置的环境变量优先,不覆盖。"""
    try:
        # utf-8-sig 兼容 Windows 记事本保存时带的 BOM
        with open(path, encoding="utf-8-sig") as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        return

    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            os.environ.setdefault(key, value)


class RefusalError(Exception):
    """模型拒绝回答本轮问题。"""


class Assistant:
    """保存对话历史,并以流式方式向 Claude 提问。"""

    def __init__(
        self,
        client: anthropic.Anthropic | None = None,
        model: str = DEFAULT_MODEL,
        effort: str = DEFAULT_EFFORT,
        system: SystemPrompt = SYSTEM_PROMPT,
    ) -> None:
        self.client = client if client is not None else anthropic.Anthropic()
        self.model = model
        self.effort = effort
        self.system = system
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
                system=self.system() if callable(self.system) else self.system,
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


def chat(assistant: Assistant, persona: Persona | None = None) -> None:
    if persona is None:
        persona = default_persona()
    print(persona.greeting)
    print(HELP_TEXT)
    while True:
        try:
            question = input("\n你:").strip()
        except (EOFError, KeyboardInterrupt):
            print(f"\n{persona.farewell}")
            return

        if not question:
            continue
        if question in ("/exit", "/quit"):
            print(persona.farewell)
            return
        if question == "/clear":
            assistant.reset()
            print("对话已清空。")
            continue

        print(f"\n{persona.name}:", end="", flush=True)
        try:
            assistant.ask(question, print_text)
            print()
        except KeyboardInterrupt:
            print("\n(已中断本次回答)")
        except RefusalError:
            print("\n抱歉,这个问题我无法回答,换个问题试试吧。")
        except anthropic.AuthenticationError:
            print(f"\nAPI Key 无效或未设置。{KEY_HINT}")
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
    parser.add_argument(
        "--persona",
        default="assistant",
        choices=PERSONA_CHOICES,
        help="助手人设:assistant 普通小助理(默认),friday 私人助手“星期五”",
    )
    parser.add_argument(
        "--call-me",
        default=None,
        help=f"“星期五”对你的称呼(默认读取环境变量 FRIDAY_CALL_ME,否则为“{DEFAULT_CALL_ME}”)",
    )
    parser.add_argument(
        "--env-file",
        default=DEFAULT_ENV_FILE,
        help="存放 API Key 的 .env 文件(默认是本程序同目录下的 .env)",
    )
    args = parser.parse_args(argv)

    load_env_file(args.env_file)
    if os.environ.get("ANTHROPIC_API_KEY") == PLACEHOLDER_KEY:
        print(f"还没有填写 API Key。请打开 {args.env_file},把“{PLACEHOLDER_KEY}”换成你的 Key。", file=sys.stderr)
        sys.exit(1)
    if args.persona == "friday":
        call_me = args.call_me or os.environ.get("FRIDAY_CALL_ME") or DEFAULT_CALL_ME
        persona = friday_persona(call_me)
    else:
        persona = default_persona()
    try:
        assistant = Assistant(model=args.model, effort=args.effort, system=persona.system)
    except anthropic.AnthropicError:
        print(f"未找到 API 凭据。{KEY_HINT}", file=sys.stderr)
        sys.exit(1)
    chat(assistant, persona)


if __name__ == "__main__":
    main()
