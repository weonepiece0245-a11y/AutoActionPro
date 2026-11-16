"""通话中不出声:你在打电话、开会时,星期五的回答只显示文字,不读出来(免得对方听到)。

两种方式:
1. 自动:电脑上有别的程序正在用麦克风(WhatsApp、Teams、Zoom、Discord、浏览器里的 Meet……),就当成你在通话。
   看的是 Windows 自己记的“哪个程序正在用麦克风”(设定 → 隐私 → 麦克风 里那份),不录音、不听内容。
2. 手动:说“我在打电话”“开会中”(用手机打电话时电脑测不到),星期五就不出声;
   说“打完了”“可以说话了”恢复,最多 2 小时自己恢复。
另外可以设定“只有用说的问才读回答”:打字问的只显示文字。
"""

from __future__ import annotations

import os
import re
import sys
import time
from typing import Callable

MANUAL_MAX = 2 * 3600  # 手动的最多多久自己恢复
CACHE_SECONDS = 3
SPEAK_MODES = ("always", "voice")  # 打字问的也读 / 只有用说的问才读
KEY = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone"
BROWSERS = ("msedge", "chrome", "firefox", "brave", "opera", "vivaldi")
FRIENDLY = {"whatsapp": "WhatsApp", "teams": "Teams", "zoom": "Zoom", "discord": "Discord", "skype": "Skype",
            "weixin": "微信", "wechat": "微信", "telegram": "Telegram", "line": "LINE", "msedge": "Edge",
            "chrome": "Chrome", "firefox": "Firefox", "obs64": "OBS", "phoneexperiencehost": "手机连接",
            "yourphone": "手机连接", "slack": "Slack", "webex": "Webex"}

START = re.compile(r"^(星期五)?[,，\s]*(我)?(现在|正)?(在|要)?(打电话|讲电话|通话|开会|视频会议|接电话)(中|了)?[,，。!！]*"
                   r"(先)?(别|不要)?(出声|说话|讲话)?[。!！]*$")
STOP = re.compile(r"^(星期五)?[,，\s]*(电话)?(打完了|讲完了|通话结束|开完会了|会开完了|可以(说话|出声|讲话)了|恢复(说话|出声|朗读))[。!！]*$")


def speak_mode() -> str:
    value = os.environ.get("FRIDAY_SPEAK_WHEN", "always").strip().lower()
    return value if value in SPEAK_MODES else "always"


def auto_enabled() -> bool:
    return os.environ.get("FRIDAY_CALL_QUIET", "on").strip().lower() not in ("off", "0", "false", "no")


def _app_name(key_name: str) -> str:
    """登录码里的名字 -> 程序名:'C:#Program Files#WhatsApp#WhatsApp.exe' -> 'whatsapp';
    'MicrosoftTeams_8wekyb3d8bbwe' -> 'microsoftteams'。"""
    name = key_name.replace("#", "\\").rsplit("\\", 1)[-1]
    name = name.split("_")[0] if "_" in name and not name.lower().endswith(".exe") else name
    return re.sub(r"\.exe$", "", name, flags=re.I).lower()


def mic_users_windows() -> list[str]:
    """现在正在用麦克风的程序(Windows 记的;LastUsedTimeStop 是 0 表示还在用)。"""
    import winreg

    found: list[str] = []

    def scan(path: str) -> None:
        try:
            root = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path)
        except OSError:
            return
        with root:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(root, i)
                except OSError:
                    break
                i += 1
                if sub == "NonPackaged":
                    continue
                try:
                    with winreg.OpenKey(root, sub) as key:
                        start = winreg.QueryValueEx(key, "LastUsedTimeStart")[0]
                        stop = winreg.QueryValueEx(key, "LastUsedTimeStop")[0]
                except OSError:
                    continue
                if start and not stop:
                    found.append(sub)

    scan(KEY)
    scan(KEY + r"\NonPackaged")
    return found


class CallGuard:
    def __init__(self, mic_users: Callable[[], list[str]] | None = None,
                 friday_listening: Callable[[], bool] = lambda: False,
                 clock: Callable[[], float] = time.time) -> None:
        if mic_users is None:
            mic_users = mic_users_windows if os.name == "nt" else (lambda: [])
        self.mic_users = mic_users
        self.friday_listening = friday_listening  # 星期五的窗口自己正在听(浏览器在用麦克风是她自己)
        self.clock = clock
        self.manual_until = 0.0
        self._cache: tuple[float, str] = (-100.0, "")
        self._own = os.path.basename(sys.executable).lower().replace(".exe", "")

    def call_app(self) -> str:
        """正在通话的程序名(给你看的名字);没有就空字串。"""
        now = self.clock()
        if now - self._cache[0] < CACHE_SECONDS:
            return self._cache[1]
        try:
            users = self.mic_users()
        except Exception:
            users = []
        app = ""
        for raw in users:
            name = _app_name(raw)
            if name.startswith("python") or name == self._own:
                continue  # 星期五自己(唤醒词、语音)
            if name in BROWSERS and self.friday_listening():
                continue  # 星期五的窗口正在听你说话
            app = FRIENDLY.get(name) or next((v for k, v in FRIENDLY.items() if k in name), "") or name
            break
        self._cache = (now, app)
        return app

    def manual(self) -> bool:
        return self.clock() < self.manual_until

    def quiet_reason(self) -> str:
        """现在要不要安静;要的话返回原因(给你看),不用就空字串。"""
        if self.manual():
            return "你说在通话中"
        if auto_enabled():
            app = self.call_app()
            if app:
                return f"{app} 正在用麦克风(像是在通话)"
        return ""

    def start_manual(self, seconds: float = MANUAL_MAX) -> None:
        self.manual_until = self.clock() + min(seconds, MANUAL_MAX)

    def stop_manual(self) -> None:
        self.manual_until = 0.0


class Commands:
    """“我在打电话”“打完了”。"""

    def __init__(self, guard: CallGuard, on_change: Callable[[bool], None] = lambda quiet: None) -> None:
        self.guard = guard
        self.on_change = on_change

    def handle(self, text: str) -> str | None:
        text = re.sub(r"\s+", "", str(text or ""))
        if START.match(text):
            self.guard.start_manual()
            self.on_change(True)
            return "好,我先不出声,回答只显示文字。打完了跟我说“打完了”(最多 2 小时自己恢复)。"
        if STOP.match(text):
            was = self.guard.manual()
            self.guard.stop_manual()
            self.on_change(False)
            return "好,可以说话了。" if was else "好的(本来就没有静音)。"
        return None
