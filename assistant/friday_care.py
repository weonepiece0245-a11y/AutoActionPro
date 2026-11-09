"""星期五主动关心你(只在 Windows 上):离开回来打招呼、用太久提醒休息、太晚提醒睡觉。

全部是固定的话(随机换着说),不问 Claude,不花钱。
看你有没有在用电脑:Windows 记录的“最后一次按键盘/动鼠标”的时间;
看你在玩什么游戏:最前面那个窗口的程序名。
"""

from __future__ import annotations

import datetime
import os
import random
import time
from typing import Callable

AWAY_SECONDS = 20 * 60  # 离开这么久再回来才打招呼
BREAK_SECONDS = 5 * 60  # 停了这么久就算休息过了,重新计时
DEFAULT_BREAK_MINUTES = 120
NIGHT_HOURS = range(1, 5)  # 凌晨 1 点到 5 点前

GAMES = {
    "r5apex.exe": "Apex", "r5apex_dx12.exe": "Apex",
    "dota2.exe": "Dota 2",
    "tslgame.exe": "PUBG",
    "titanfall2.exe": "Titanfall 2",
    "javaw.exe": "Minecraft", "minecraft.windows.exe": "Minecraft",
    "valorant.exe": "Valorant", "valorant-win64-shipping.exe": "Valorant",
    "cs2.exe": "CS2", "leagueoflegends.exe": "英雄联盟", "league of legends.exe": "英雄联盟",
    "genshinimpact.exe": "原神", "yuanshen.exe": "原神",
}

WELCOME = [
    "{name},欢迎回来。", "{name}回来啦。", "欢迎回来,{name},我一直在。", "{name},你回来了,有什么要我做的吗?",
]
BREAK = [
    "{name},你已经连续用电脑{hours}了,起来走走、喝口水吧。",
    "{name},连续{hours}了,让眼睛休息一下,看看远处。",
    "{name},已经{hours}没停了,站起来伸个懒腰吧。",
]
BREAK_GAME = [
    "{name},{game} 已经打了{hours}了,这局结束休息一下,喝口水吧。",
    "{name},{game} 连续{hours}了,打完这局起来走走?",
    "{name},{game} 打了{hours},眼睛和手都休息一下吧。",
]
NIGHT = [
    "{name},已经 {time} 了,早点休息吧,明天再继续。",
    "{name},现在 {time},夜深了,别太晚睡哦。",
]


def care_settings() -> dict:
    """齿轮设定里的选项(存在 .env)。"""
    try:
        minutes = int(os.environ.get("FRIDAY_CARE_BREAK", "") or DEFAULT_BREAK_MINUTES)
    except ValueError:
        minutes = DEFAULT_BREAK_MINUTES
    return {
        "welcome": os.environ.get("FRIDAY_CARE_WELCOME", "on") != "off",
        "break_minutes": minutes if minutes in (0, 60, 90, 120, 180) else DEFAULT_BREAK_MINUTES,
        "night": os.environ.get("FRIDAY_CARE_NIGHT", "on") != "off",
    }


def hours_text(seconds: float) -> str:
    minutes = int(seconds // 60)
    hours, rest = divmod(minutes, 60)
    if hours and rest >= 15:
        return f"{hours}个半小时" if 15 <= rest < 45 else f"{hours + 1}个小时"
    return f"{hours}个小时" if hours else f"{minutes}分钟"


# ======================== Windows ========================


def idle_seconds() -> float | None:
    """离最后一次按键盘、动鼠标过了多少秒;不是 Windows 返回 None。"""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(info)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        return None
    now = ctypes.windll.kernel32.GetTickCount() & 0xFFFFFFFF
    return ((now - info.dwTime) & 0xFFFFFFFF) / 1000


def foreground_app() -> str | None:
    """最前面那个窗口的程序名(小写,例如 r5apex.exe)。"""
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        hwnd = user32.GetForegroundWindow()
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        handle = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return None
        try:
            buffer = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(len(buffer))
            if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return None
            return os.path.basename(buffer.value).lower()
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return None


# ======================== 判断 ========================


class Care:
    """每 30 秒看一次你在不在用电脑,该说话时调用 say(文字) -> 有没有说出去(忙的时候返回 False,下次再说)。"""

    def __init__(self, say: Callable[[str], bool], name: Callable[[], str],
                 idle: Callable[[], float | None] = idle_seconds, app: Callable[[], str | None] = foreground_app,
                 reminders: Callable[[], list[str]] = lambda: [], settings: Callable[[], dict] = care_settings,
                 clock: Callable[[], float] = time.time,
                 now: Callable[[], datetime.datetime] = datetime.datetime.now, rng=random) -> None:
        self.say = say
        self.name = name
        self.idle = idle
        self.app = app
        self.reminders = reminders  # 今天接下来的提醒(给“欢迎回来”用)
        self.settings = settings
        self.clock = clock
        self.now = now
        self.rng = rng
        self.active_since: float | None = None  # 这一段连续使用从什么时候开始
        self.last_break_nudge: float | None = None
        self.away = False
        self.night_done = ""  # 哪一晚提醒过了

    def _line(self, options: list[str], **values) -> str:
        return self.rng.choice(options).format(name=self.name(), **values)

    def tick(self) -> str | None:
        """看一次;说了话就返回那句话。"""
        idle = self.idle()
        if idle is None:
            return None
        now = self.clock()
        settings = self.settings()
        if idle >= AWAY_SECONDS:
            self.away = True
        if idle >= BREAK_SECONDS:
            self.active_since = None  # 休息过了,重新计时
            self.last_break_nudge = None
            return None
        if self.active_since is None:
            self.active_since = now - idle

        # 1. 离开一阵子后回来
        if self.away:
            if not settings["welcome"]:
                self.away = False
            else:
                text = self._line(WELCOME)
                coming = self.reminders()
                if coming:
                    text += f"今天还有 {len(coming)} 个提醒,下一个是 {coming[0]}。"
                if self.say(text):
                    self.away = False
                    return text
                return None

        # 2. 太晚了(每晚一次)
        local = self.now()
        night_key = (local - datetime.timedelta(hours=5)).strftime("%Y-%m-%d")
        if settings["night"] and local.hour in NIGHT_HOURS and self.night_done != night_key:
            text = self._line(NIGHT, time=local.strftime("%H:%M"))
            if self.say(text):
                self.night_done = night_key
                return text
            return None

        # 3. 用太久(之后每过同样久再提醒一次)
        minutes = settings["break_minutes"]
        if minutes:
            used = now - self.active_since
            since_nudge = now - (self.last_break_nudge or self.active_since)
            if used >= minutes * 60 and since_nudge >= minutes * 60:
                game = GAMES.get((self.app() or "").lower())
                hours = hours_text(used)
                text = self._line(BREAK_GAME, game=game, hours=hours) if game else self._line(BREAK, hours=hours)
                if self.say(text):
                    self.last_break_nudge = now
                    return text
        return None

    def start(self, interval: float = 30) -> None:
        import threading

        def loop():
            while True:
                try:
                    self.tick()
                except Exception:
                    pass
                time.sleep(interval)

        threading.Thread(target=loop, daemon=True, name="friday-care").start()
