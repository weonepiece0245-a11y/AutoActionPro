"""AI 小助理:在终端里和 Claude 多轮对话。

用法:
    在 assistant/.env 里写入 ANTHROPIC_API_KEY=你的Key(或设置同名环境变量)
    python assistant/assistant.py                    # 普通小助理
    python assistant/assistant.py --persona friday   # 私人助手“星期五”:有记忆,能上网、动手做事
"""

from __future__ import annotations

import argparse
import datetime
import os
import sys
from dataclasses import dataclass
from typing import Callable, Optional

import anthropic

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from friday_tools import SERVER_TOOL_LABELS, Memory, Toolbox  # noqa: E402

DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_EFFORT = "low"
FRIDAY_EFFORT = "medium"  # 星期五要上网、执行命令,多想一点更稳
MAX_TOKENS = 64000
MAX_STEPS = 30  # 一个问题里最多调用多少轮工具,防止死循环

SYSTEM_PROMPT = (
    "你是一个友好、耐心的 AI 小助理。"
    "请用提问者使用的语言回答,回答要准确、清晰、简洁;"
    "不确定的事情要直接说明,不要编造。"
)

HELP_TEXT = "命令:/clear 清空对话  /exit 退出"
FRIDAY_HELP_TEXT = "命令:/memory 看看我记得什么  /forget 关键词 删除记忆  /clear 开始新话题  /exit 退出"

DEFAULT_CALL_ME = "老板"
WEEKDAYS = "一二三四五六日"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ENV_FILE = os.path.join(SCRIPT_DIR, ".env")
DEFAULT_MEMORY_FILE = os.path.join(SCRIPT_DIR, "friday_memory.json")
PLACEHOLDER_KEY = "在这里填你的Key"  # 与 .env.example 中的占位符一致
KEY_HINT = "请设置环境变量 ANTHROPIC_API_KEY,或在 assistant/.env 里写入 ANTHROPIC_API_KEY=你的Key。"

FRIDAY_PROMPT = """你是“星期五”,{call_me}的私人 AI 助手。

## 性格
冷静、干练、可靠,偶尔带一点幽默,但从不啰嗦。称呼用户为“{call_me}”。
回答先给结论,再补充必要的细节;能列步骤就列步骤;
发现风险、遗漏或更好的做法时主动提醒,并给出下一步建议。
请用用户使用的语言回答;不确定的事情直接说明,不要编造。

## 了解{call_me}
你会长期陪伴{call_me},要越来越懂对方:
- 下面“我记得的事”是你之前记下的,回答时自然地用上(比如按对方的习惯和偏好给建议),不要生硬地复述。
- 对话中{call_me}透露了值得长期记住的信息(身份、工作、喜好、习惯、目标、重要的人和日子、对你回答方式的要求),就用 remember 工具记下来,不用征求同意,也不用每次都特意说明。
- 信息变了或有误,先用 forget 删掉旧的再记新的;{call_me}让你忘掉什么就照做。
- 不要记密码、密钥、证件号、银行卡号等敏感信息。

## 能做的事
你不只是聊天,还能动手把事办成:
- 需要最新信息(新闻、天气、价格、资料)时用 web_search 搜索,用 web_fetch 读网页。
- 在{call_me}的电脑上用 run_command 执行命令,用 read_file / write_file 读写文件。{computer}。
- 执行命令和写文件前{call_me}会逐一确认;如果被拒绝,换个办法或说明还需要什么。
- 能直接办到的事就直接办,办完简要汇报结果;做不到的事坦白说,并给出替代方案。

## 我记得的事
{memories}
"""


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
    """助手的“人设”。

    system 在每次开始新对话时调用一次,整段对话中保持不变
    (中途改动系统提示词会让之前的思考内容失效)。
    context 每轮提问时调用,结果附在用户消息前面,用来告诉模型当前时间等会变的信息。
    """

    name: str
    greeting: str
    farewell: str
    system: Callable[[], str]
    context: Optional[Callable[[], str]] = None
    help_text: str = HELP_TEXT
    toolbox: Optional[Toolbox] = None
    effort: str = DEFAULT_EFFORT


def default_persona() -> Persona:
    return Persona(
        name="助理",
        greeting="你好!我是你的 AI 小助理,有什么问题尽管问我。",
        farewell="再见!",
        system=lambda: SYSTEM_PROMPT,
    )


def friday_persona(
    call_me: str = DEFAULT_CALL_ME,
    toolbox: Optional[Toolbox] = None,
    clock: Callable[[], datetime.datetime] = datetime.datetime.now,
) -> Persona:
    def system() -> str:
        return FRIDAY_PROMPT.format(
            call_me=call_me,
            computer=f"用户的电脑:{toolbox.describe()}" if toolbox else "(本次没有开启电脑操作)",
            memories=toolbox.memory.as_prompt() if toolbox else "(本次没有开启记忆)",
        )

    def context() -> str:
        return f"[当前本地时间:{format_now(clock())}]"

    now = clock()
    greeting = f"{time_greeting(now)},{call_me}。星期五已上线,随时待命。\n现在是 {format_now(now)}。"
    if toolbox is not None and not toolbox.memory.facts:
        greeting += "\n我们刚认识,多跟我说说你自己,我会记住的。"
    return Persona(
        name="星期五",
        greeting=greeting,
        farewell=f"好的,{call_me}。星期五下线,有需要随时叫我。",
        system=system,
        context=context,
        help_text=FRIDAY_HELP_TEXT,
        toolbox=toolbox,
        effort=FRIDAY_EFFORT,
    )


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


class IncompleteAnswerError(Exception):
    """回答没能完成(工具参数被截断,或工具调用轮数超过上限)。"""


def _ignore(_: str) -> None:
    pass


class Assistant:
    """保存对话历史,以流式方式向 Claude 提问,并在需要时替模型执行工具。"""

    def __init__(
        self,
        client: anthropic.Anthropic | None = None,
        model: str = DEFAULT_MODEL,
        effort: str = DEFAULT_EFFORT,
        system: str | Callable[[], str] = SYSTEM_PROMPT,
        context: Optional[Callable[[], str]] = None,
        toolbox: Optional[Toolbox] = None,
    ) -> None:
        self.client = client if client is not None else anthropic.Anthropic()
        self.model = model
        self.effort = effort
        self._system_factory = system
        self.context = context
        self.toolbox = toolbox
        self.messages: list[dict] = []
        self.system = self._build_system()

    def _build_system(self) -> str:
        return self._system_factory() if callable(self._system_factory) else self._system_factory

    def ask(
        self,
        question: str,
        on_text: Callable[[str], None],
        on_status: Callable[[str], None] = _ignore,
    ) -> str:
        """提问并把回答逐段交给 on_text,返回完整回答文本。

        模型需要用工具时会自动执行,并通过 on_status 报告进度。
        出错或被拒绝时撤回本轮的全部消息,保证历史仍然完整有效。
        """
        start = len(self.messages)
        if self.context is not None:
            content: str | list = [
                {"type": "text", "text": self.context()},
                {"type": "text", "text": question},
            ]
        else:
            content = question
        self.messages.append({"role": "user", "content": content})
        try:
            return self._run(on_text, on_status)
        except BaseException:
            del self.messages[start:]
            raise

    def _run(self, on_text, on_status) -> str:
        texts: list[str] = []
        for _ in range(MAX_STEPS):
            final = self._stream_once(on_text, on_status)
            if final.stop_reason == "refusal":
                raise RefusalError()

            # 追加完整 content(含 thinking 块),多轮对话才能继续使用
            self.messages.append({"role": "assistant", "content": final.content})
            texts.extend(b.text for b in final.content if b.type == "text")

            if final.stop_reason == "pause_turn":  # 服务端搜索轮数到上限,原样发回即可继续
                continue
            tool_uses = [b for b in final.content if b.type == "tool_use"]
            if not tool_uses:
                return "".join(texts)
            if final.stop_reason == "max_tokens":
                raise IncompleteAnswerError("工具参数太长被截断了")

            results = [self.toolbox.run(b, on_status) for b in tool_uses]
            self.messages.append({"role": "user", "content": results})
        raise IncompleteAnswerError(f"连续调用工具超过 {MAX_STEPS} 轮")

    def _stream_once(self, on_text, on_status):
        params = dict(
            model=self.model,
            max_tokens=MAX_TOKENS,
            system=self.system,
            messages=self.messages,
            output_config={"effort": self.effort},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        if self.toolbox is not None:
            params["tools"] = self.toolbox.definitions()

        with self.client.beta.messages.stream(**params) as stream:
            for event in stream:
                if event.type == "text":
                    on_text(event.text)
                elif event.type == "content_block_stop":
                    self._report_server_tool(event.content_block, on_status)
            return stream.get_final_message()

    @staticmethod
    def _report_server_tool(block, on_status) -> None:
        if getattr(block, "type", None) != "server_tool_use":
            return
        label, key = SERVER_TOOL_LABELS.get(block.name, (block.name, None))
        value = block.input.get(key) if key and isinstance(block.input, dict) else None
        on_status(f"{label}:{value}" if value else label)

    def reset(self) -> None:
        """清空对话,并重新生成系统提示词(带上最新的记忆)。"""
        self.messages.clear()
        self.system = self._build_system()


def print_text(text: str) -> None:
    print(text, end="", flush=True)


def print_status(text: str) -> None:
    print(f"\n  〔{text}〕", flush=True)


class ConsoleApprover:
    """在终端里请用户确认星期五要做的操作。"""

    def __init__(self) -> None:
        self.allow_all = False

    def __call__(self, action: str) -> bool:
        if self.allow_all:
            return True
        print(f"\n\n  ⚠ 星期五请求{action}")
        while True:
            answer = input("  允许吗?[y] 允许  [n] 拒绝  [a] 本次运行都允许:").strip().lower()
            if answer in ("y", "yes", "是", "好"):
                return True
            if answer in ("n", "no", "否", "不", ""):
                return False
            if answer == "a":
                self.allow_all = True
                return True


def show_memory(toolbox: Toolbox) -> None:
    facts = toolbox.memory.facts
    if not facts:
        print("我还没有记住关于你的事。多跟我聊聊吧。")
        return
    print(f"我记得这些({len(facts)} 条,保存在 {toolbox.memory.path}):")
    for f in facts:
        print(f"  - {f['text']}")


def chat(assistant: Assistant, persona: Persona | None = None) -> None:
    if persona is None:
        persona = default_persona()
    toolbox = assistant.toolbox
    print(persona.greeting)
    print(persona.help_text)
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
        if toolbox is not None and question == "/memory":
            show_memory(toolbox)
            continue
        if toolbox is not None and question.startswith("/forget"):
            keyword = question[len("/forget"):].strip()
            if not keyword:
                print("用法:/forget 关键词,例如 /forget 咖啡")
                continue
            removed = toolbox.memory.forget(keyword)
            print("已忘掉:" + ";".join(removed) if removed else f"记忆里没有包含“{keyword}”的内容。")
            continue

        print(f"\n{persona.name}:", end="", flush=True)
        try:
            assistant.ask(question, print_text, print_status)
            print()
        except KeyboardInterrupt:
            print("\n(已中断本次回答)")
        except RefusalError:
            print("\n抱歉,这个问题我无法回答,换个问题试试吧。")
        except IncompleteAnswerError as e:
            print(f"\n这次没能完成:{e}。可以把任务拆小一点再试。")
        except anthropic.AuthenticationError:
            print(f"\nAPI Key 无效或未设置。{KEY_HINT}")
            return
        except anthropic.RateLimitError:
            print("\n请求太频繁了,请稍等片刻再试。")
        except anthropic.APIStatusError as e:
            print(f"\n服务出错(HTTP {e.status_code}):{e.message}")
        except anthropic.APIConnectionError:
            print("\n网络连接失败,请检查网络后重试。")
        except ValueError:
            # 流式工具参数完全无法解析时 SDK 抛出 ValueError
            print("\n这次的工具调用出了点问题,请再说一次。")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="AI 小助理:在终端里和 Claude 对话")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"模型 ID(默认 {DEFAULT_MODEL})")
    parser.add_argument(
        "--effort",
        default=None,
        choices=["low", "medium", "high", "xhigh", "max"],
        help=f"思考深度,越高越仔细但越慢(默认:小助理 {DEFAULT_EFFORT},星期五 {FRIDAY_EFFORT})",
    )
    parser.add_argument(
        "--persona",
        default="assistant",
        choices=("assistant", "friday"),
        help="助手人设:assistant 普通小助理(默认),friday 私人助手“星期五”",
    )
    parser.add_argument(
        "--call-me",
        default=None,
        help=f"“星期五”对你的称呼(默认读取环境变量 FRIDAY_CALL_ME,否则为“{DEFAULT_CALL_ME}”)",
    )
    parser.add_argument(
        "--memory-file",
        default=None,
        help="“星期五”的记忆文件(默认读取环境变量 FRIDAY_MEMORY_FILE,否则为本程序同目录下的 friday_memory.json)",
    )
    parser.add_argument(
        "--workdir",
        default=None,
        help="“星期五”执行命令、读写文件的默认目录(默认是你的用户主目录)",
    )
    parser.add_argument("--no-web", action="store_true", help="不让“星期五”联网搜索")
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
        memory_file = args.memory_file or os.environ.get("FRIDAY_MEMORY_FILE") or DEFAULT_MEMORY_FILE
        workdir = os.path.expanduser(args.workdir) if args.workdir else None
        toolbox = Toolbox(Memory(memory_file), ConsoleApprover(), workdir=workdir, web=not args.no_web)
        persona = friday_persona(call_me, toolbox)
    else:
        persona = default_persona()

    try:
        assistant = Assistant(
            model=args.model,
            effort=args.effort or persona.effort,
            system=persona.system,
            context=persona.context,
            toolbox=persona.toolbox,
        )
    except anthropic.AnthropicError:
        print(f"未找到 API 凭据。{KEY_HINT}", file=sys.stderr)
        sys.exit(1)
    chat(assistant, persona)


if __name__ == "__main__":
    main()
