"""Telegram:手机上多一个找星期五的地方,跟 Discord 一样能打字、发语音、发照片、收提醒。

在 Telegram 里找 @BotFather,发 /newbot 建一个机器人,把它给的钥匙(token)填进齿轮设定 → 连接的应用。
只认私聊;要先发电脑上显示的 6 位配对码。配对、拉黑、手机遥控锁(验证器数字)都和 Discord 一样。
钥匙存在 .env,不会出现在记录和错误信息里。
"""

from __future__ import annotations

import json
import mimetypes
import os
import queue
import re
import secrets
import threading
import time
import urllib.error
import urllib.request
from typing import Callable

from friday_discord import DiscordGate, split_message

API = "https://api.telegram.org"
MAX_DOWNLOAD = 20 * 1024 * 1024  # Telegram 机器人最多能下载 20MB 的文件
TOKEN_RE = re.compile(r"^\d{5,12}:[A-Za-z0-9_-]{30,}$")
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


class TelegramError(Exception):
    pass


def token_ok(token: str) -> bool:
    return bool(TOKEN_RE.match(token.strip()))


def _multipart(fields: dict, files: dict) -> tuple[bytes, str]:
    boundary = "----friday" + secrets.token_hex(12)
    parts = []
    for name, value in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    for name, (filename, data) in files.items():
        kind = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        safe = re.sub(r'["\r\n]', "", filename)
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{safe}"\r\n'
                     f"Content-Type: {kind}\r\n\r\n".encode() + data + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _http(url: str, body: bytes | None, content_type: str | None, timeout: float) -> dict:
    headers = {"Content-Type": content_type} if content_type else {}
    request = urllib.request.Request(url, data=body, headers=headers, method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:  # 网址里有钥匙:错误信息里不带网址
        try:
            data = json.loads(e.read().decode("utf-8"))
        except (ValueError, OSError):
            data = {}
        raise TelegramError(f"Telegram 回了错误(HTTP {e.code}):{str(data.get('description', ''))[:100]}") from None
    except (urllib.error.URLError, OSError, ValueError):
        raise TelegramError("连不上 Telegram") from None


def _download(url: str, timeout: float = 60) -> bytes:
    try:
        with urllib.request.urlopen(urllib.request.Request(url), timeout=timeout) as response:  # noqa: S310
            return response.read(MAX_DOWNLOAD + 1)
    except (urllib.error.URLError, OSError):
        raise TelegramError("从 Telegram 下载文件失败") from None


class TelegramGate(DiscordGate):
    SERVICE = "Telegram"
    PAIRED_REPLY = "配对成功!以后直接在这里发消息、发语音或照片给我就行。"


class TelegramBridge:
    """在后台用长轮询收 Telegram 消息,交给和 Discord 一样的处理流程。"""

    def __init__(self, token: str, events: queue.Queue, state_file: str | os.PathLike,
                 on_status: Callable[[str], None] = print, http=_http, download=_download) -> None:
        self.token = token.strip()
        self.on_status = on_status
        self.http = http
        self.download = download
        self.gate = TelegramGate(state_file, events, self.send, on_status, send_voice=self.send_voice)
        self.tts: Callable[[str], bytes | None] | None = None
        self.offset = 0
        self.running = False
        self.bot_name = ""

    # ---------- 发 ----------
    def _call(self, method: str, fields: dict | None = None, files: dict | None = None, timeout: float = 30) -> dict:
        url = f"{API}/bot{self.token}/{method}"
        if files:
            body, kind = _multipart({k: str(v) for k, v in (fields or {}).items()}, files)
        else:
            body, kind = json.dumps(fields or {}).encode(), "application/json"
        data = self.http(url, body, kind, timeout)
        if not data.get("ok"):
            raise TelegramError(f"Telegram:{str(data.get('description', '没成功'))[:100]}")
        return data.get("result")

    def check(self) -> str:
        """钥匙对不对:返回机器人的 @名字。"""
        me = self._call("getMe")
        self.bot_name = me.get("username", "")
        return self.bot_name

    def send(self, chat_id, text: str, audio: bytes | None = None, files: list | None = None) -> None:
        for part in split_message(text or "", 4000):
            if part.strip():
                self._call("sendMessage", {"chat_id": chat_id, "text": part})
        if audio:
            self._call("sendAudio", {"chat_id": chat_id, "title": "星期五"}, {"audio": ("friday.mp3", audio)}, timeout=60)
        for name, data in files or []:
            if os.path.splitext(name)[1].lower() in IMAGE_EXTS and len(data) < 10 * 1024 * 1024:
                self._call("sendPhoto", {"chat_id": chat_id}, {"photo": (name, data)}, timeout=120)
            else:
                self._call("sendDocument", {"chat_id": chat_id}, {"document": (name, data)}, timeout=120)

    def send_voice(self, chat_id, text: str) -> None:
        audio = None
        if self.tts is not None:
            try:
                audio = self.tts(text)
            except Exception:
                audio = None
        self.send(chat_id, text, audio=audio)

    def notify_owners(self, text: str, audio: bytes | None = None, files: list | None = None) -> int:
        """主动发给所有配对过的账号(私聊的 chat id 就是账号 id)。"""
        sent = 0
        for owner_id in list(self.gate.owners):
            try:
                self.send(owner_id, text, audio=audio, files=files)
                sent += 1
            except TelegramError as e:
                self.on_status(f"Telegram 消息没发出去:{e}")
        return sent

    # ---------- 收 ----------
    def _file(self, file_id: str) -> bytes | None:
        info = self._call("getFile", {"file_id": file_id})
        path = info.get("file_path")
        if not path or info.get("file_size", 0) > MAX_DOWNLOAD:
            return None
        data = self.download(f"{API}/file/bot{self.token}/{path}")
        return data if len(data) <= MAX_DOWNLOAD else None

    def handle_update(self, update: dict) -> None:
        message = update.get("message") or {}
        chat = message.get("chat") or {}
        sender = message.get("from") or {}
        if not message or not sender or sender.get("is_bot"):
            return
        if chat.get("type") != "private":
            return  # 群组里大家都看得到:只认私聊
        author_id = int(sender["id"])
        name = sender.get("username") or " ".join(x for x in (sender.get("first_name"), sender.get("last_name")) if x) or str(author_id)
        text = message.get("text") or message.get("caption") or ""
        audio, images = None, []
        if author_id in self.gate.owners:  # 陌生人发的语音、照片不下载
            try:
                voice = message.get("voice")  # 只听语音消息(音乐文件不拿来识别)
                if voice:
                    audio = self._file(voice["file_id"])
                photos = message.get("photo") or []
                if photos:
                    best = max(photos, key=lambda p: p.get("file_size", 0) or p.get("width", 0))
                    data = self._file(best["file_id"])
                    if data:
                        images.append(data)
                document = message.get("document") or {}
                if str(document.get("mime_type", "")).startswith("image/"):
                    data = self._file(document["file_id"])
                    if data:
                        images.append(data)
            except TelegramError as e:
                self.send(chat["id"], f"这个文件拿不到:{e}")
                return
        if not text and audio is None and not images:
            return
        self.gate.handle(author_id, name, True, text, chat["id"], audio=audio, images=images or None)

    def poll_once(self) -> None:
        updates = self._call("getUpdates", {"offset": self.offset, "timeout": 25, "allowed_updates": ["message"]},
                             timeout=40) or []
        for update in updates:
            self.offset = max(self.offset, int(update.get("update_id", 0)) + 1)
            try:
                self.handle_update(update)
            except Exception as e:  # noqa: BLE001
                self.on_status(f"Telegram 消息处理出错:{type(e).__name__}")

    def start(self) -> None:
        self.running = True

        def loop() -> None:
            try:
                self.on_status(f"Telegram 已连接:@{self.check()}")
            except TelegramError as e:
                self.on_status(f"Telegram 连不上:{e}")
            wait = 2.0
            while self.running:
                try:
                    self.poll_once()
                    wait = 2.0
                except TelegramError:
                    time.sleep(wait)
                    wait = min(wait * 2, 60)

        threading.Thread(target=loop, daemon=True, name="friday-telegram").start()

    def stop(self) -> None:
        self.running = False
