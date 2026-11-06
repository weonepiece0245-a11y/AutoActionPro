"""在手机上通过 Discord 私信和“星期五”说话。

- 机器人只在私信里工作,只听配对过的主人一个人的话,其他人的消息一律忽略。
- 第一次使用要配对:电脑上显示 6 位配对码,主人在 Discord 私信机器人发送这个码。
- “星期五”需要用户确认的操作(执行命令、关程序、发邮件……)会发到 Discord 问主人,
  主人回复 y 才执行,2 分钟没回复算拒绝。

设置:双击 discord-setup.bat(python friday_discord.py setup),按提示粘贴机器人的 Token。
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import queue
import secrets
import sys
import threading
import webbrowser
from pathlib import Path
from typing import Callable

MAX_MESSAGE = 1900  # Discord 单条消息上限 2000 字,留点余量
ASK_TIMEOUT = 120  # 秒
YES = ("y", "yes", "是", "好", "允许", "可以", "ok", "确定")
INVITE_PERMISSIONS = 1024 + 2048 + 32768  # 查看频道、发消息、上传文件

StatusFn = Callable[[str], None]


def split_message(text: str, limit: int = MAX_MESSAGE) -> list[str]:
    """把长回答切成 Discord 能发的几段,尽量在换行处断开。"""
    text = text.strip() or "(没有内容)"
    parts = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = limit
        parts.append(text[:cut].rstrip())
        text = text[cut:].lstrip("\n")
    parts.append(text)
    return parts


def bot_id_from_token(token: str) -> str | None:
    """机器人 Token 的第一段是机器人 id 的 base64。"""
    first = token.strip().split(".")[0]
    try:
        value = base64.b64decode(first + "=" * (-len(first) % 4)).decode()
    except Exception:
        return None
    return value if value.isdigit() else None


def invite_url(bot_id: str) -> str:
    return f"https://discord.com/oauth2/authorize?client_id={bot_id}&scope=bot&permissions={INVITE_PERMISSIONS}"


class DiscordMessage:
    """一条来自主人的消息,附带回复和请求确认的方法(在聊天线程里调用)。"""

    def __init__(self, text: str, reply: Callable[[str], None], ask: Callable[[str], bool]) -> None:
        self.text = text
        self.reply = reply
        self.ask = ask


class DiscordGate:
    """决定一条私信该怎么处理:配对、忽略陌生人、回答确认、还是交给“星期五”。

    和 discord.py 无关,方便测试。send(channel, text) 用来回消息。
    """

    def __init__(self, state_file: str | os.PathLike, events: queue.Queue, send, on_status: StatusFn = print) -> None:
        self.state_file = Path(state_file)
        self.events = events
        self.send = send
        self.on_status = on_status
        self.owner_id = self._load_owner()
        self.pair_code = None if self.owner_id else f"{secrets.randbelow(10 ** 6):06d}"
        self.answers: queue.Queue = queue.Queue()
        self.waiting = False

    def _load_owner(self) -> int | None:
        try:
            owner = json.loads(self.state_file.read_text(encoding="utf-8")).get("owner_id")
        except (OSError, ValueError, AttributeError):
            return None
        return int(owner) if isinstance(owner, (int, str)) and str(owner).isdigit() else None

    def _save_owner(self, owner_id: int, name: str) -> None:
        self.state_file.write_text(json.dumps({"owner_id": owner_id, "owner_name": name}, ensure_ascii=False),
                                   encoding="utf-8")

    def handle(self, author_id: int, author_name: str, is_dm: bool, text: str, channel) -> None:
        if not is_dm:
            return
        text = (text or "").strip()
        if self.owner_id is None:
            if text == self.pair_code:
                self.owner_id = author_id
                self.pair_code = None
                self._save_owner(author_id, author_name)
                self.on_status(f"已和 Discord 用户 {author_name} 配对,以后只听 TA 的消息")
                self.send(channel, "配对成功!以后在这里私信我就行,我只听你一个人的。")
            else:
                self.send(channel, "还没有配对。请发送“星期五”电脑窗口里显示的 6 位配对码。")
            return
        if author_id != self.owner_id:
            self.on_status(f"有陌生人({author_name})私信了机器人,已忽略")
            return
        if not text:
            return
        if self.waiting:
            self.answers.put(text)
            return
        self.events.put(("discord", DiscordMessage(
            text,
            reply=lambda answer: self.send(channel, answer),
            ask=lambda question: self.ask(channel, question),
        )))

    def ask(self, channel, question: str, timeout: float = ASK_TIMEOUT) -> bool:
        """在 Discord 上请主人确认,等主人回复;超时算拒绝。"""
        while not self.answers.empty():
            self.answers.get_nowait()
        self.waiting = True
        try:
            self.send(channel, f"⚠ {question}\n回复 y 允许,回复其他内容就是拒绝({int(timeout)} 秒内不回复也算拒绝)。")
            try:
                answer = self.answers.get(timeout=timeout)
            except queue.Empty:
                self.send(channel, "没有收到回复,已取消这个操作。")
                return False
        finally:
            self.waiting = False
        return answer.strip().lower() in YES


class DiscordBridge:
    """在后台线程里运行 Discord 机器人,把主人的消息放进“星期五”的事件队列。"""

    def __init__(self, token: str, events: queue.Queue, state_file: str | os.PathLike,
                 on_status: StatusFn = print) -> None:
        self.token = token.strip()
        self.on_status = on_status
        self.loop: asyncio.AbstractEventLoop | None = None
        self.gate = DiscordGate(state_file, events, self.send, on_status)

    def start(self) -> None:
        try:
            import discord  # noqa: F401
        except ImportError:
            self.on_status("Discord 功能需要先安装依赖:python -m pip install -r assistant/requirements.txt")
            return
        threading.Thread(target=self._run, daemon=True, name="friday-discord").start()

    def _run(self) -> None:
        import discord

        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        client = discord.Client(intents=discord.Intents.default())  # 私信内容不需要特殊权限

        @client.event
        async def on_ready():
            self.on_status(f"Discord 已连接:{client.user}")
            if self.gate.pair_code:
                self.on_status(f"还没配对:在 Discord 私信机器人 {client.user},发送配对码 {self.gate.pair_code}")

        @client.event
        async def on_message(message):
            if message.author.bot:
                return
            self.gate.handle(message.author.id, str(message.author),
                             isinstance(message.channel, discord.DMChannel), message.content, message.channel)

        try:
            self.loop.run_until_complete(client.start(self.token))
        except discord.LoginFailure:
            self.on_status("Discord 机器人的 Token 无效,请双击 discord-setup.bat 重新设置")
        except Exception as e:
            self.on_status(f"Discord 连接断开:{type(e).__name__}: {e}")

    def send(self, channel, text: str) -> None:
        """从任何线程发消息(在机器人的事件循环里执行)。"""
        if self.loop is None:
            return
        for part in split_message(text):
            future = asyncio.run_coroutine_threadsafe(channel.send(part), self.loop)
            try:
                future.result(timeout=30)
            except Exception as e:
                self.on_status(f"发送 Discord 消息失败:{e}")
                return


# ======================== 设置向导 ========================


def save_env_value(env_file: Path, key: str, value: str) -> None:
    """在 .env 里写入或替换一行 KEY=VALUE。"""
    try:
        lines = env_file.read_text(encoding="utf-8-sig").splitlines()
    except FileNotFoundError:
        lines = []
    lines = [line for line in lines if not line.strip().startswith(f"{key}=")]
    lines.append(f"{key}={value}")
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")


def setup(folder: str | os.PathLike | None = None, ask: Callable[[str], str] = input,
          open_url: Callable[[str], object] = webbrowser.open) -> int:
    folder = Path(folder or os.path.dirname(os.path.abspath(__file__)))
    print("== 设置 Discord 机器人 ==\n")
    print("1. 打开 https://discord.com/developers/applications ,点右上角 New Application,名字填 Friday,创建。")
    print("2. 点左侧 Bot,再点 Reset Token,确认后点 Copy 复制 Token。")
    print("   (Token 相当于机器人的密码,不要发给任何人。)\n")
    token = ask("3. 把 Token 粘贴到这里(在窗口里点右键就能粘贴),然后按回车:").strip().strip('"')
    bot_id = bot_id_from_token(token)
    if token.count(".") != 2 or bot_id is None:
        print("\n这看起来不像机器人的 Token。请回到 Bot 页面重新 Reset Token 并复制,再运行一次。")
        return 1
    save_env_value(folder / ".env", "FRIDAY_DISCORD_TOKEN", token)
    (folder / "friday_discord.json").unlink(missing_ok=True)  # 换了机器人就重新配对
    url = invite_url(bot_id)
    print("\n已保存 Token。")
    print("4. 接下来浏览器会打开邀请页面:选一个你自己的服务器,点“授权”,把机器人加进去。")
    print("   (要私信机器人,你和它至少得在同一个服务器里。没有自己的服务器的话,先在 Discord 左侧点 + 新建一个。)")
    print(f"   如果浏览器没打开,手动打开这个网址:{url}")
    open_url(url)
    print("\n5. 然后打开“星期五”,窗口里会显示 6 位配对码;在 Discord 里点开机器人头像发私信,发送这个配对码就完成了。")
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "setup":
        sys.exit(setup())
    print("用法:python friday_discord.py setup")
