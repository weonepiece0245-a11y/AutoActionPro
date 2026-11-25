"""星期五的两把锁:

1. 手机遥控锁:用手机上的验证器 App(Google Authenticator、Microsoft Authenticator 等)每 30 秒变一次的 6 位数字。
   Discord 超过 2 小时没用要先输入数字解锁;执行命令、发邮件这类要确认的操作也要输入数字(不是回复 y)。
   就算 Discord 账号被人盗了、聊天记录被人看到,旧的数字也早就失效了。打错 3 次就暂停手机遥控,要在电脑上恢复。
2. 窗口锁:离开电脑一段时间,星期五的窗口就锁住,要输入 PIN 才能继续用。

PIN 只存加盐的哈希值,不存原文;验证器的密钥存在你电脑的 .env 里。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import struct
import time
from typing import Callable

TOTP_STEP = 30
TOTP_DIGITS = 6
REMOTE_SESSION_SECONDS = 2 * 60 * 60  # 手机遥控解锁后这么久没用就要重新输入
REMOTE_MAX_WRONG = 3
PIN_ITERATIONS = 200_000
LOCAL_MAX_WRONG = 5
LOCAL_COOLDOWN = 5 * 60
LOCK_MINUTES = (0, 5, 10, 30)
ISSUER = "Friday"

Clock = Callable[[], float]


# ======================== 验证器的 6 位数字(TOTP,RFC 6238) ========================


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _key(secret: str) -> bytes:
    clean = secret.replace(" ", "").upper()
    return base64.b32decode(clean + "=" * (-len(clean) % 8))


def totp(secret: str, counter: int) -> str:
    digest = hmac.new(_key(secret), struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(number % 10 ** TOTP_DIGITS).zfill(TOTP_DIGITS)


def setup_uri(secret: str, account: str = "星期五") -> str:
    from urllib.parse import quote

    return f"otpauth://totp/{quote(ISSUER)}:{quote(account)}?secret={secret}&issuer={quote(ISSUER)}&digits=6&period=30"


def group(secret: str) -> str:
    return " ".join(secret[i:i + 4] for i in range(0, len(secret), 4))


def matching_counter(secret: str, code: str, now: float, drift: int = 1) -> int | None:
    """code 对得上就返回它的时间格编号(前后各容许 30 秒误差);对不上返回 None。"""
    code = "".join(c for c in str(code) if c.isdigit())
    if len(code) != TOTP_DIGITS:
        return None
    current = int(now // TOTP_STEP)
    for counter in range(current - drift, current + drift + 1):
        if hmac.compare_digest(totp(secret, counter), code):
            return counter
    return None


class RemoteLock:
    """手机遥控锁。secret() 返回验证器密钥(没设就是没开)。"""

    def __init__(self, secret: Callable[[], str] = lambda: os.environ.get("FRIDAY_REMOTE_TOTP", "").strip(),
                 clock: Clock = time.time, on_alert: Callable[[str], None] | None = None) -> None:
        self.secret = secret
        self.clock = clock
        self.on_alert = on_alert
        self.unlocked_at: float | None = None
        self.last_used = 0.0
        self.last_counter = -1  # 用过的数字不能再用一次
        self.wrong = 0
        self.paused = False

    def enabled(self) -> bool:
        return bool(self.secret())

    def session_open(self) -> bool:
        if not self.enabled():
            return True
        now = self.clock()
        if self.paused or self.unlocked_at is None or now - self.last_used > REMOTE_SESSION_SECONDS:
            return False
        return True

    def touch(self) -> None:
        self.last_used = self.clock()

    def check(self, code: str) -> str:
        """"ok" / "wrong" / "paused"。对了顺便解锁(2 小时内有效)。"""
        if self.paused:
            return "paused"
        counter = matching_counter(self.secret(), code, self.clock())
        if counter is None or counter <= self.last_counter:
            self.wrong += 1
            if self.wrong >= REMOTE_MAX_WRONG:
                self.paused = True
                self.unlocked_at = None
                if self.on_alert:
                    self.on_alert(f"手机遥控的验证码连续错了 {self.wrong} 次,已暂停手机遥控。"
                                  "如果不是你自己打错的,可能有人拿到了你的 Discord 账号;请在电脑上的齿轮设定 → 安全 里恢复")
                return "paused"
            return "wrong"
        self.last_counter = counter
        self.wrong = 0
        self.unlocked_at = self.last_used = self.clock()
        return "ok"

    def resume(self) -> None:
        self.paused = False
        self.wrong = 0
        self.unlocked_at = None

    def lock_now(self) -> None:
        self.unlocked_at = None


# ======================== PIN(窗口锁) ========================


def hash_pin(pin: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), bytes.fromhex(salt), PIN_ITERATIONS).hex()
    return f"pbkdf2${PIN_ITERATIONS}${salt}${digest}"


def check_pin(pin: str, stored: str) -> bool:
    try:
        kind, rounds, salt, digest = stored.split("$")
        if kind != "pbkdf2":
            return False
        given = hashlib.pbkdf2_hmac("sha256", str(pin).encode("utf-8"), bytes.fromhex(salt), int(rounds)).hex()
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(given, digest)


def valid_pin(pin: str) -> bool:
    return str(pin).isdigit() and 4 <= len(str(pin)) <= 8


def lock_minutes() -> int:
    try:
        value = int(os.environ.get("FRIDAY_LOCK_MINUTES", "0") or 0)
    except ValueError:
        return 0
    return value if value in LOCK_MINUTES else 0


class WindowLock:
    """窗口锁:离开太久就锁住,输入 PIN 解锁。"""

    def __init__(self, stored: Callable[[], str] = lambda: os.environ.get("FRIDAY_LOCK_PIN", "").strip(),
                 clock: Clock = time.monotonic, on_alert: Callable[[str], None] | None = None) -> None:
        self.stored = stored
        self.clock = clock
        self.on_alert = on_alert
        self.locked = False
        self.wrong = 0
        self.blocked_until = 0.0

    def enabled(self) -> bool:
        return bool(self.stored()) and lock_minutes() > 0

    def has_pin(self) -> bool:
        return bool(self.stored())

    def lock(self) -> bool:
        if not self.has_pin():
            return False
        self.locked = True
        return True

    def unlock(self, pin: str) -> str:
        """"ok" / "wrong" / "wait:秒数"。"""
        if not self.locked:
            return "ok"
        now = self.clock()
        if now < self.blocked_until:
            return f"wait:{int(self.blocked_until - now) + 1}"
        if check_pin(pin, self.stored()):
            self.locked = False
            self.wrong = 0
            return "ok"
        self.wrong += 1
        if self.wrong >= LOCAL_MAX_WRONG:
            self.wrong = 0
            self.blocked_until = now + LOCAL_COOLDOWN
            if self.on_alert:
                self.on_alert(f"有人在电脑上连续 {LOCAL_MAX_WRONG} 次输错星期五的解锁 PIN,已锁定 5 分钟")
            return f"wait:{LOCAL_COOLDOWN}"
        return "wrong"
