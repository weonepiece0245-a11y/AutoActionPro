"""在手机上通过 Discord 私信和“星期五”说话。

- 机器人只听配对过的账号(可以是主人的几个号),其他人的消息一律忽略。
  可以私信它;在服务器里,在名字带 friday / 星期五 的频道说话,或在别的频道 @它。
- 第一次使用要配对:电脑上显示 6 位配对码,在 Discord 私信机器人发送这个码。
  想再加一个号:在“星期五”窗口输入 /discord pair 生成新配对码(10 分钟有效、只能用一次)。
- “星期五”需要用户确认的操作(执行命令、关程序、发邮件……)会发到 Discord 问主人,
  主人回复 y 才执行,2 分钟没回复算拒绝。

设置:双击 discord-setup.bat(python friday_discord.py setup),按提示粘贴机器人的 Token。
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import queue
import re
import secrets
import sys
import threading
import time
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


def is_friday_channel(name: str | None) -> bool:
    name = (name or "").lower()
    return "friday" in name or "星期五" in name


def invite_url(bot_id: str) -> str:
    return f"https://discord.com/oauth2/authorize?client_id={bot_id}&scope=bot&permissions={INVITE_PERMISSIONS}"


AUDIO_SUFFIXES = (".ogg", ".oga", ".opus", ".mp3", ".m4a", ".wav", ".webm", ".aac", ".flac")
MAX_AUDIO_BYTES = 20 * 1024 * 1024


def is_audio_attachment(filename: str, content_type: str | None) -> bool:
    return (content_type or "").lower().startswith("audio/") or filename.lower().endswith(AUDIO_SUFFIXES)


class DiscordMessage:
    """一条来自主人的消息,附带回复和请求确认的方法(在聊天线程里调用)。

    audio:语音消息的原始音频(没有就是 None);reply_voice:回复文字并附上朗读的语音。
    """

    def __init__(self, text: str, reply: Callable[[str], None], ask: Callable[[str], bool],
                 audio: bytes | None = None, reply_voice: Callable[[str], None] | None = None) -> None:
        self.text = text
        self.reply = reply
        self.ask = ask
        self.audio = audio
        self.reply_voice = reply_voice or reply


class DiscordGate:
    """决定一条私信该怎么处理:配对、忽略陌生人、回答确认、还是交给“星期五”。

    可以配对多个账号(都是主人自己的号)。新的配对码只能在电脑上生成,
    第一次没有任何账号时会自动生成一个;之后用 /discord pair 生成,10 分钟内有效、只能用一次。
    和 discord.py 无关,方便测试。send(channel, text) 用来回消息。
    """

    PAIR_MINUTES = 10

    def __init__(self, state_file: str | os.PathLike, events: queue.Queue, send, on_status: StatusFn = print,
                 clock: Callable[[], float] = time.monotonic, send_voice=None,
                 transcriber: Callable[[bytes], str | None] | None = None) -> None:
        self.state_file = Path(state_file)
        self.events = events
        self.send = send
        self.send_voice = send_voice  # (channel, text):回复文字并附上语音
        self.transcriber = transcriber  # 把语音消息转成文字(用于语音回答确认问题)
        self.on_status = on_status
        self.clock = clock
        self.owners: dict[int, str] = self._load_owners()
        self.pair_code: str | None = None
        self.pair_expires: float | None = None
        if not self.owners:
            self.pair_code = self._code()  # 第一次:一直有效,直到配对成功
        self.answers: queue.Queue = queue.Queue()
        self.waiting_for: int | None = None

    @staticmethod
    def _code() -> str:
        return f"{secrets.randbelow(10 ** 6):06d}"

    def _load_owners(self) -> dict[int, str]:
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        owners = {}
        for item in data.get("owners", []):
            if isinstance(item, dict) and str(item.get("id", "")).isdigit():
                owners[int(item["id"])] = str(item.get("name", ""))
        old = data.get("owner_id")  # 旧版只存一个主人
        if str(old or "").isdigit():
            owners.setdefault(int(old), str(data.get("owner_name", "")))
        return owners

    def _save_owners(self) -> None:
        data = {"owners": [{"id": i, "name": n} for i, n in self.owners.items()]}
        self.state_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---- 电脑上的管理命令 ----
    def new_pair_code(self) -> str:
        self.pair_code = self._code()
        self.pair_expires = self.clock() + self.PAIR_MINUTES * 60
        return self.pair_code

    def owner_list(self) -> list[str]:
        return [f"{n}. {name or '(未知名字)'}(id {i})" for n, (i, name) in enumerate(self.owners.items(), 1)]

    def remove_owner(self, key: str) -> list[str]:
        key = key.strip()
        items = list(self.owners.items())
        if key.isdigit() and 1 <= int(key) <= len(items):
            targets = [items[int(key) - 1]]
        else:
            targets = [(i, n) for i, n in items if key and (key in n or key == str(i))]
        for owner_id, _ in targets:
            del self.owners[owner_id]
        if targets:
            self._save_owners()
        return [name or str(i) for i, name in targets]

    def _pair_code_valid(self) -> bool:
        return self.pair_code is not None and (self.pair_expires is None or self.clock() <= self.pair_expires)

    # ---- 收到私信 ----
    def handle(self, author_id: int, author_name: str, is_dm: bool, text: str, channel,
               mentioned: bool = False, named_channel: bool = False, audio: bytes | None = None) -> None:
        """is_dm:私信;mentioned:消息里 @ 了机器人;named_channel:频道名带 friday / 星期五。

        服务器频道里只有 @ 机器人,或在专用频道里说话时才处理,免得和朋友聊天时它插嘴。
        """
        if not (is_dm or mentioned or named_channel):
            return
        text = (text or "").strip()
        if author_id not in self.owners:
            if self._pair_code_valid() and text == self.pair_code:
                self.owners[author_id] = author_name
                self.pair_code = self.pair_expires = None
                self._save_owners()
                self.on_status(f"已和 Discord 账号 {author_name} 配对(现在共 {len(self.owners)} 个账号)")
                self.send(channel, "配对成功!以后私信我,或在名字带 friday / 星期五 的频道里说话,或 @我 都可以。")
            elif not self.owners:
                self.send(channel, "还没有配对。请发送“星期五”电脑窗口里显示的 6 位配对码。")
            else:
                self.on_status(f"有没配对的账号({author_name})找机器人说话,已忽略")
            return
        if not text and not audio:
            return
        if self.waiting_for == author_id:
            self.answers.put(audio if audio and not text else text)
            return
        send_voice = self.send_voice
        self.events.put(("discord", DiscordMessage(
            text,
            reply=lambda answer: self.send(channel, answer),
            ask=lambda question: self.ask(channel, question, author_id),
            audio=audio,
            reply_voice=(lambda answer: send_voice(channel, answer)) if send_voice else None,
        )))

    def ask(self, channel, question: str, author_id: int | None = None, timeout: float = ASK_TIMEOUT) -> bool:
        """在 Discord 上请发消息的那个账号确认,等它回复;超时算拒绝。"""
        while not self.answers.empty():
            self.answers.get_nowait()
        self.waiting_for = author_id
        try:
            self.send(channel, f"⚠ {question}\n回复 y 允许,回复其他内容就是拒绝({int(timeout)} 秒内不回复也算拒绝)。")
            try:
                answer = self.answers.get(timeout=timeout)
            except queue.Empty:
                self.send(channel, "没有收到回复,已取消这个操作。")
                return False
        finally:
            self.waiting_for = None
        if isinstance(answer, bytes):  # 用语音回答的
            try:
                answer = (self.transcriber(answer) if self.transcriber else None) or ""
            except Exception:
                answer = ""
            if answer:
                self.send(channel, f"🎤 听到:{answer}")
        return answer.strip().lower().rstrip("。.!!") in YES


class DiscordBridge:
    """在后台线程里运行 Discord 机器人,把主人的消息放进“星期五”的事件队列。"""

    def __init__(self, token: str, events: queue.Queue, state_file: str | os.PathLike,
                 on_status: StatusFn = print) -> None:
        self.token = token.strip()
        self.on_status = on_status
        self.loop: asyncio.AbstractEventLoop | None = None
        self.gate = DiscordGate(state_file, events, self.send, on_status, send_voice=self.send_voice)
        self.tts: Callable[[str], bytes | None] | None = None  # 文字转 MP3,由主程序设置
        self.client = None

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
        # 先尝试读取服务器频道里的消息内容(需要在开发者页面打开 Message Content Intent);
        # 没打开的话退回只用私信和 @机器人(这两种不需要特殊权限)
        intents = discord.Intents.default()
        intents.message_content = True
        try:
            self.loop.run_until_complete(self._make_client(discord, intents).start(self.token))
        except discord.PrivilegedIntentsRequired:
            self.on_status("开发者页面还没打开 Message Content Intent:现在只能私信机器人,或在频道里 @它")
            try:
                self.loop.run_until_complete(self._make_client(discord, discord.Intents.default()).start(self.token))
            except Exception as e:
                self._report(discord, e)
        except Exception as e:
            self._report(discord, e)

    def _report(self, discord, error: Exception) -> None:
        if isinstance(error, discord.LoginFailure):
            self.on_status("Discord 机器人的 Token 无效,请双击 discord-setup.bat 重新设置")
        else:
            self.on_status(f"Discord 连接断开:{type(error).__name__}: {error}")

    def _make_client(self, discord, intents):
        client = discord.Client(intents=intents)
        self.client = client

        @client.event
        async def on_ready():
            self.on_status(f"Discord 已连接:{client.user}")
            if self.gate.pair_code:
                self.on_status(f"还没配对:私信机器人 {client.user},发送配对码 {self.gate.pair_code}")

        @client.event
        async def on_message(message):
            if message.author.bot:
                return
            mentioned = client.user in message.mentions
            text = re.sub(rf"<@!?{client.user.id}>", "", message.content or "").strip()
            audio = None
            for attachment in message.attachments:  # 语音消息是一个音频附件
                if is_audio_attachment(attachment.filename, attachment.content_type):
                    if attachment.size <= MAX_AUDIO_BYTES:
                        audio = await attachment.read()
                    break
            self.gate.handle(
                message.author.id, str(message.author), isinstance(message.channel, discord.DMChannel), text,
                message.channel, mentioned=mentioned, named_channel=is_friday_channel(getattr(message.channel, "name", "")),
                audio=audio,
            )

        return client

    def send_voice(self, channel, text: str) -> None:
        """回复文字,再附上一段朗读这段文字的语音(在聊天线程里调用)。"""
        audio = None
        if self.tts is not None:
            try:
                audio = self.tts(text)
            except Exception:
                audio = None
        self.send(channel, text, audio=audio)

    def send(self, channel, text: str, audio: bytes | None = None) -> None:
        """从任何线程发消息。在机器人自己的线程里调用时只安排发送、不等待(等待会把自己卡死)。"""
        if self.loop is None:
            return
        parts = split_message(text)

        async def send_all():
            for part in parts:
                await channel.send(part)
            if audio:
                import discord

                await channel.send(file=discord.File(io.BytesIO(audio), filename="friday.mp3"))

        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is self.loop:
            task = self.loop.create_task(send_all())
            task.add_done_callback(self._log_failure)
            return
        future = asyncio.run_coroutine_threadsafe(send_all(), self.loop)
        try:
            future.result(timeout=60)
        except Exception as e:
            self.on_status(f"发送 Discord 消息失败:{type(e).__name__}: {e}")

    def notify_owners(self, text: str, audio: bytes | None = None) -> int:
        """主动私信所有配对过的账号(比如提醒到点了),返回成功发出的个数。在聊天线程里调用。"""
        if self.loop is None or self.client is None:
            self.on_status(f"Discord 还没连上,这条消息没发出去:{text[:50]}")
            return 0
        owners = list(self.gate.owners)

        async def send_all() -> int:
            sent = 0
            for owner_id in owners:
                try:
                    user = self.client.get_user(owner_id) or await self.client.fetch_user(owner_id)
                    for part in split_message(text):
                        await user.send(part)
                    if audio:
                        import discord

                        await user.send(file=discord.File(io.BytesIO(audio), filename="friday.mp3"))
                    sent += 1
                except Exception as e:  # 比如对方关了私信
                    self.on_status(f"私信 {owner_id} 失败:{type(e).__name__}: {e}")
            return sent

        try:
            return asyncio.run_coroutine_threadsafe(send_all(), self.loop).result(timeout=120)
        except Exception as e:
            self.on_status(f"发送 Discord 私信失败:{type(e).__name__}: {e}")
            return 0

    def _log_failure(self, task) -> None:
        if not task.cancelled() and task.exception() is not None:
            error = task.exception()
            self.on_status(f"发送 Discord 消息失败:{type(error).__name__}: {error}")


# ======================== 设置向导 ========================


TOKEN_KEY = "FRIDAY_DISCORD_TOKEN"


def set_discord_enabled(env_file: str | os.PathLike, enabled: bool) -> bool:
    """在 .env 里打开或关闭这台电脑上的 Discord 机器人(注释掉 Token 那一行),返回是否找到了 Token。

    搬到云端后,电脑上要关掉,否则两个“星期五”会同时回复。
    """
    env_file = Path(env_file)
    try:
        lines = env_file.read_text(encoding="utf-8-sig").splitlines()
    except FileNotFoundError:
        return False
    found = False
    for i, line in enumerate(lines):
        bare = line.strip().lstrip("#").strip()
        if bare.startswith(f"{TOKEN_KEY}=") and bare != f"{TOKEN_KEY}=":
            lines[i] = bare if enabled else f"#{bare}"
            found = True
    if found:
        env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return found


def read_token(env_file: str | os.PathLike) -> str | None:
    """从环境变量或 .env 读 Token;被 /discord off 注释掉的也认(云端模式用)。"""
    token = os.environ.get(TOKEN_KEY, "").strip()
    if token:
        return token
    try:
        lines = Path(env_file).read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return None
    for line in lines:
        bare = line.strip().lstrip("#").strip()
        if bare.startswith(f"{TOKEN_KEY}="):
            value = bare.split("=", 1)[1].strip().strip('"').strip("'")
            if value:
                return value
    return None


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
