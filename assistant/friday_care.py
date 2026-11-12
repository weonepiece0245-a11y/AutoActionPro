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

# 这些全屏了也不算游戏(看视频、放幻灯片、星期五自己的窗口)
NOT_GAMES = {
    "chrome.exe", "msedge.exe", "firefox.exe", "opera.exe", "brave.exe", "vivaldi.exe",
    "vlc.exe", "potplayermini64.exe", "potplayermini.exe", "mpc-hc64.exe", "mpc-be64.exe", "wmplayer.exe",
    "video.ui.exe", "microsoft.media.player.exe", "applicationframehost.exe", "netflix.exe",
    "powerpnt.exe", "explorer.exe", "python.exe", "pythonw.exe", "discord.exe", "spotify.exe",
}
VIDEO_APPS = NOT_GAMES - {"powerpnt.exe", "explorer.exe", "python.exe", "pythonw.exe"}
# 游戏模式绝不自动关掉的(系统和星期五自己)
NEVER_CLOSE = {"explorer", "python", "pythonw", "dwm", "csrss", "winlogon", "svchost", "system", "steam"}
GAME_GRACE = 90  # 秒:游戏离开最前面这么久才算打完(切出去回个消息不算)

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
HELD = "{name},刚才你在打{game},有 {count} 条提醒我先替你记着:{items}。"


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
        "game_quiet": os.environ.get("FRIDAY_GAME_QUIET", "on") != "off",
        "game_close": game_close_list(),
        "game_boost": os.environ.get("FRIDAY_GAME_BOOST", "on") != "off",
        "game_report": os.environ.get("FRIDAY_GAME_REPORT", "on") != "off",
    }


def clean_program_name(name: str) -> str:
    """“Chrome.exe ” -> “chrome”;不像程序名的返回空字符串。"""
    name = str(name).strip().strip('"').lower()
    if name.endswith(".exe"):
        name = name[:-4]
    return name if name and len(name) <= 60 and all(c.isalnum() or c in "-_. " for c in name) else ""


def game_close_list() -> list[str]:
    """开游戏时自动关掉的程序(进程名,不带 .exe)。"""
    names = [clean_program_name(n) for n in os.environ.get("FRIDAY_GAME_CLOSE", "").split(",")]
    return list(dict.fromkeys(n for n in names if n and n not in NEVER_CLOSE))


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
    path = foreground_path()
    return os.path.basename(path).lower() if path else None


def foreground_path() -> str | None:
    """最前面那个窗口的程序的完整路径。"""
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
            return buffer.value or None
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return None


def is_fullscreen() -> bool:
    """最前面的窗口是不是占满了整个屏幕(全屏游戏、全屏视频)。"""
    if os.name != "nt":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                        ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

        user32 = ctypes.windll.user32
        user32.GetForegroundWindow.restype = ctypes.c_void_p
        user32.MonitorFromWindow.restype = ctypes.c_void_p
        user32.MonitorFromWindow.argtypes = [ctypes.c_void_p, wintypes.DWORD]
        user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(MONITORINFO)]
        user32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.RECT)]
        user32.GetClassNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return False
        name = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, name, 64)
        if name.value in ("Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
            return False  # 桌面和任务栏
        rect, info = wintypes.RECT(), MONITORINFO()
        info.cbSize = ctypes.sizeof(info)
        monitor = user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)) or not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return False
        m = info.rcMonitor
        return rect.left <= m.left and rect.top <= m.top and rect.right >= m.right and rect.bottom >= m.bottom
    except Exception:
        return False


def display_requested() -> bool:
    """有没有程序要求屏幕别熄灭(浏览器、播放器在放视频时会这样要求)。"""
    if os.name != "nt":
        return False
    try:
        import ctypes

        state = ctypes.c_ulong(0)
        status = ctypes.windll.powrprof.CallNtPowerInformation(16, None, 0, ctypes.byref(state), ctypes.sizeof(state))
        return status == 0 and bool(state.value & 0x2)  # SystemExecutionState / ES_DISPLAY_REQUIRED
    except Exception:
        return False


def game_name(exe: str, fullscreen: bool, path: str | None = None, known=None) -> str | None:
    """认识的游戏用名字(学过的、装在游戏文件夹里的也算);不认识但全屏的程序也当游戏(用程序名)。"""
    exe = (exe or "").lower()
    if known is not None:
        if known.is_not_game(exe):
            return None
        name = known.name(exe, path)
        if name:
            return name
    if exe in GAMES:
        return GAMES[exe]
    if fullscreen and exe and exe not in NOT_GAMES:
        return os.path.splitext(exe)[0]
    return None


def look(idle: Callable[[], float | None] = idle_seconds, app: Callable[[], str | None] = foreground_app,
         fullscreen: Callable[[], bool] = is_fullscreen, display: Callable[[], bool] = display_requested,
         path: Callable[[], str | None] | None = None, known=None) -> dict | None:
    """看一眼你在干什么:{idle 没碰键盘鼠标几秒, app, game 在玩的游戏, watching 在看视频/全屏, present_idle}。

    看视频、打游戏时不碰键盘鼠标也算你在电脑前(present_idle 记为 0)。
    """
    seconds = idle()
    if seconds is None:
        return None
    exe = (app() or "").lower()
    full_path = path() if path is not None else None
    if full_path and not exe:
        exe = os.path.basename(full_path).lower()
    full = bool(fullscreen())
    game = game_name(exe, full, full_path, known)
    watching = full or (exe in VIDEO_APPS and bool(display()))
    return {"idle": seconds, "app": exe, "path": full_path, "fullscreen": full, "game": game, "watching": watching,
            "present_idle": 0.0 if (game or watching) else seconds}


def presence_idle() -> float | None:
    """当成“离开多久了”的秒数:看视频、打游戏时是 0;不是 Windows 返回 None。"""
    snapshot = look()
    return None if snapshot is None else snapshot["present_idle"]


# ======================== 判断 ========================


class Care:
    """每 30 秒看一次你在不在用电脑,该说话时调用 say(文字) -> 有没有说出去(忙的时候返回 False,下次再说)。

    打游戏时(设定“游戏时别打扰”开着):不读出来,改成右下角小通知 toast(文字);
    提醒先记着,打完(游戏离开最前面一阵子)再一起说。刚开游戏时调用 on_game(游戏名)(自动游戏模式)。
    """

    def __init__(self, say: Callable[[str], bool], name: Callable[[], str],
                 idle: Callable[[], float | None] = idle_seconds, app: Callable[[], str | None] = foreground_app,
                 reminders: Callable[[], list[str]] = lambda: [], settings: Callable[[], dict] = care_settings,
                 clock: Callable[[], float] = time.time,
                 now: Callable[[], datetime.datetime] = datetime.datetime.now, rng=random,
                 fullscreen: Callable[[], bool] = is_fullscreen, display: Callable[[], bool] = display_requested,
                 toast: Callable[[str], None] = lambda text: None,
                 on_game: Callable[[str], None] = lambda game: None,
                 on_game_end: Callable[[str], None] = lambda game: None) -> None:
        self.say = say
        self.name = name
        self.idle = idle
        self.app = app
        self.fullscreen = fullscreen
        self.display = display
        self.toast = toast
        self.on_game = on_game
        self.on_game_end = on_game_end
        self.reminders = reminders  # 今天接下来的提醒(给“欢迎回来”用)
        self.settings = settings
        self.clock = clock
        self.now = now
        self.rng = rng
        self.active_since: float | None = None  # 这一段连续使用从什么时候开始
        self.last_break_nudge: float | None = None
        self.away = False
        self.night_done = ""  # 哪一晚提醒过了
        self.game: str | None = None  # 正在玩的游戏
        self.game_seen = 0.0
        self.held: list[str] = []  # 打游戏时先记着的提醒
        self.screentime = None  # 电脑使用时间统计(friday_screentime.ScreenTime)
        self.focus = None  # 专注模式(friday_focus.Focus):专注时不提醒休息、不打招呼
        self.known = None  # 学过的游戏(friday_games.KnownGames)
        self.app_now = ""  # 最近一次看到在最前面的程序(开游戏时给游戏报告用)
        self.app_path: Callable[[], str | None] | None = None  # 最前面程序的完整路径(Windows 上才设)

    def _line(self, options: list[str], **values) -> str:
        return self.rng.choice(options).format(name=self.name(), **values)

    # ---------- 打游戏时 ----------
    def quiet_now(self) -> bool:
        """现在在打游戏、而且开着“游戏时别打扰”。"""
        return bool(self.game) and self.settings().get("game_quiet", True)

    def hold(self, text: str, short: str | None = None) -> None:
        """打游戏时来的提醒:右下角小通知,先记着,打完再说。"""
        try:
            self.toast(text)
        except Exception:
            pass
        self.held.append(short or text)

    def _tell(self, text: str) -> bool:
        """关心的话:打游戏时只弹小通知(过时了就不补说),平时读出来。"""
        if self.quiet_now():
            try:
                self.toast(text)
            except Exception:
                pass
            return True
        return self.say(text)

    def _watch_game(self, game: str | None, now: float) -> str | None:
        if game:
            self.game_seen = now
            if self.game != game:
                started = self.game is None
                self.game = game
                if started:
                    try:
                        self.on_game(game)
                    except Exception:
                        pass
            return None
        if self.game is None or now - self.game_seen < GAME_GRACE:
            return None
        finished, self.game = self.game, None
        try:
            self.on_game_end(finished)
        except Exception:
            pass
        if not self.held:
            return None
        text = HELD.format(name=self.name(), game=finished, count=len(self.held), items=";".join(self.held))
        if self.say(text):
            self.held = []
            return text
        self.game = None
        return None

    # ---------- 每 30 秒 ----------
    def tick(self) -> str | None:
        """看一次;说了话就返回那句话。"""
        snapshot = look(self.idle, self.app, self.fullscreen, self.display, path=self.app_path, known=self.known)
        if snapshot is None:
            return None
        self.app_now = snapshot["app"]
        if self.known is not None:
            try:
                learned = self.known.seen(snapshot["app"], snapshot.get("path"), snapshot.get("fullscreen", False),
                                          NOT_GAMES)
            except Exception:
                learned = None
            if learned:
                try:
                    self.toast("🎮 " + learned)
                except Exception:
                    pass
        if self.screentime is not None:
            try:
                self.screentime.record(snapshot)
            except Exception:
                pass
        now = self.clock()
        settings = self.settings()
        said = self._watch_game(snapshot["game"], now)
        if said:
            return said
        if self.held and not self.game:  # 上次没说出去(正忙),再试
            text = HELD.format(name=self.name(), game="游戏", count=len(self.held), items=";".join(self.held))
            if self.say(text):
                self.held = []
                return text
        idle = snapshot["present_idle"]  # 看视频、打游戏时不算离开
        if idle >= AWAY_SECONDS:
            self.away = True
        if idle >= BREAK_SECONDS:
            self.active_since = None  # 休息过了,重新计时
            self.last_break_nudge = None
            return None
        if self.active_since is None:
            self.active_since = now - idle

        focusing = self.focus is not None and self.focus.active()
        # 1. 离开一阵子后回来
        if self.away and focusing:
            self.away = False
        if self.away:
            if not settings["welcome"] or self.quiet_now():  # 回来就直接开游戏:不打扰
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
            if self._tell(text):
                self.night_done = night_key
                return text
            return None

        # 3. 用太久(之后每过同样久再提醒一次)
        minutes = settings["break_minutes"]
        if minutes and not focusing:
            used = now - self.active_since
            since_nudge = now - (self.last_break_nudge or self.active_since)
            if used >= minutes * 60 and since_nudge >= minutes * 60:
                game = self.game or GAMES.get(snapshot["app"])
                hours = hours_text(used)
                text = self._line(BREAK_GAME, game=game, hours=hours) if game else self._line(BREAK, hours=hours)
                if self._tell(text):
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
