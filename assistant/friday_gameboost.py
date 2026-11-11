"""游戏优化:开游戏时让游戏先用电脑,后台程序让路;打完自动恢复(不花钱)。

开游戏时(Apex、Dota 2 或任何全屏游戏):
  1. 游戏设成“高”优先级(和任务管理器里右键 → 设置优先级一样);
  2. 浏览器、云盘同步、别的游戏平台、聊天软件这类后台程序降成“低于正常”,它们照常运行,只是让游戏先用 CPU;
     Discord、Spotify、OBS、显卡驱动、防作弊和系统程序不动;
  3. 电源计划换成“高性能”(电脑有这个计划才换);
  4. 星期五自己也让路,大的后台工作(文件夹备份)等打完再做。
游戏打完(离开最前面 90 秒)全部改回原样;星期五中途意外关掉,下次打开时也会改回去。
不关任何程序(要关的程序在“开游戏时自动关掉”里另外选)。存在 friday_gameboost.json。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path

STATE_FILE = "friday_gameboost.json"

# 优先级(Windows 的数字)
IDLE, BELOW_NORMAL, NORMAL, ABOVE_NORMAL, HIGH = 0x40, 0x4000, 0x20, 0x8000, 0x80

# 打游戏时让路的后台程序(进程名,不带 .exe,小写)
YIELD = {
    "chrome", "msedge", "firefox", "opera", "opera_gx", "brave", "vivaldi",
    "onedrive", "dropbox", "googledrivefs", "icloudservices", "icloudphotos", "megasync",
    "epicgameslauncher", "eadesktop", "eabackgroundservice", "origin", "battle.net", "ubisoftconnect",
    "upc", "galaxyclient", "riotclientservices", "goggalaxy", "itunes",
    "ms-teams", "teams", "slack", "telegram", "whatsapp", "wechat", "weixin", "line", "skype",
    "creative cloud", "adobe desktop service", "adobeipcbroker", "acrotray", "ccxprocess",
    "wallpaper32", "wallpaper64", "lively", "phoneexperiencehost", "widgets", "yourphone",
    "everything", "qbittorrent", "utorrent", "bittorrent", "idman",
}
# 绝不碰的(语音、音乐、录像、显卡、防作弊、系统、星期五的守护)
NEVER = {
    "discord", "spotify", "obs64", "obs32", "obs", "audiodg", "steam", "steamwebhelper",
    "nvcontainer", "nvidia share", "nvidia overlay", "nvsphelper64", "amdow", "radeonsoftware",
    "easyanticheat", "easyanticheat_eos", "beservice", "vgc", "vgtray", "faceitclient", "faceit",
    "explorer", "dwm", "csrss", "winlogon", "svchost", "system", "lsass", "services", "wininit",
    "python", "pythonw", "msmpeng",
}
POWER_PLANS = {  # Windows 自带计划的固定编号
    "balanced": "381b4222-f694-41f0-9685-ff5bb260df2e",
    "high": "8c5e7fda-e8bf-4a96-9a85-cf48a7f6f1ef",
    "ultimate": "e9a42b02-d5df-448d-aa00-03f14749eb61",
    "saver": "a1841308-3541-4fab-bc81-f71556f20b4a",
}
FAST_PLAN_NAMES = ("ultimate performance", "卓越性能", "high performance", "高性能")
GUID = re.compile(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", re.I)

_gaming = threading.Event()


def gaming() -> bool:
    """现在是不是在打游戏(星期五的大后台工作看这个,打完再做)。"""
    return _gaming.is_set()


def enabled() -> bool:
    return os.environ.get("FRIDAY_GAME_BOOST", "on") != "off"


def _base(name: str) -> str:
    name = str(name or "").strip().lower()
    return name[:-4] if name.endswith(".exe") else name


# ======================== Windows ========================


class WindowsSystem:
    """真的去改优先级、电源计划(只在 Windows 上)。"""

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        self.ctypes, self.wintypes = ctypes, wintypes
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel.OpenProcess.restype = wintypes.HANDLE
        self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel.GetPriorityClass.argtypes = [wintypes.HANDLE]
        self.kernel.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        self.kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        self.kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
        self.kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
        self.user.GetForegroundWindow.restype = wintypes.HWND
        self.user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]

    def processes(self) -> list[tuple[int, str]]:
        ctypes, wintypes = self.ctypes, self.wintypes

        class Entry(ctypes.Structure):
            _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                        ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                        ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                        ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD),
                        ("szExeFile", ctypes.c_wchar * 260)]

        snap = self.kernel.CreateToolhelp32Snapshot(0x2, 0)
        if not snap or snap == wintypes.HANDLE(-1).value:
            return []
        found = []
        try:
            entry = Entry()
            entry.dwSize = ctypes.sizeof(Entry)
            ok = self.kernel.Process32FirstW(snap, ctypes.byref(entry))
            while ok:
                found.append((int(entry.th32ProcessID), entry.szExeFile))
                ok = self.kernel.Process32NextW(snap, ctypes.byref(entry))
        finally:
            self.kernel.CloseHandle(snap)
        return found

    def foreground_pid(self) -> int | None:
        hwnd = self.user.GetForegroundWindow()
        if not hwnd:
            return None
        pid = self.wintypes.DWORD()
        self.user.GetWindowThreadProcessId(hwnd, self.ctypes.byref(pid))
        return int(pid.value) or None

    def get_priority(self, pid: int) -> int | None:
        handle = self.kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return None
        try:
            return int(self.kernel.GetPriorityClass(handle)) or None
        finally:
            self.kernel.CloseHandle(handle)

    def set_priority(self, pid: int, value: int) -> bool:
        handle = self.kernel.OpenProcess(0x0200 | 0x1000, False, pid)  # PROCESS_SET_INFORMATION
        if not handle:
            return False  # 防作弊保护的游戏、别的用户的程序:改不了就算了
        try:
            return bool(self.kernel.SetPriorityClass(handle, value))
        finally:
            self.kernel.CloseHandle(handle)

    @staticmethod
    def _powercfg(*args: str) -> str:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            proc = subprocess.run(["powercfg", *args], capture_output=True, timeout=15, creationflags=flags)
        except (OSError, subprocess.SubprocessError):
            return ""
        for encoding in ("utf-8", "mbcs", "gbk"):
            try:
                return proc.stdout.decode(encoding)
            except (UnicodeDecodeError, LookupError):
                continue
        return proc.stdout.decode("utf-8", errors="replace")

    def power_plans(self) -> tuple[str | None, list[tuple[str, str]]]:
        """(现在用的计划编号, [(编号, 名字)])。"""
        active = GUID.search(self._powercfg("/getactivescheme"))
        plans = []
        for line in self._powercfg("/list").splitlines():
            found = GUID.search(line)
            if found:
                name = re.search(r"\((.+?)\)", line[found.end():])
                plans.append((found.group(1).lower(), name.group(1) if name else ""))
        return (active.group(1).lower() if active else None), plans

    def set_power_plan(self, guid: str) -> bool:
        if not GUID.fullmatch(guid):
            return False
        self._powercfg("/setactive", guid)
        return (self.power_plans()[0] or "") == guid.lower()


# ======================== 游戏优化 ========================


def fast_plan(plans: list[tuple[str, str]]) -> tuple[str, str] | None:
    """电脑上最快的电源计划(卓越性能 > 高性能);没有就 None。"""
    for wanted in (POWER_PLANS["ultimate"], POWER_PLANS["high"]):
        for guid, name in plans:
            if guid == wanted:
                return guid, name
    for words in FAST_PLAN_NAMES:
        for guid, name in plans:
            if words in name.lower():
                return guid, name
    return None


class GameBoost:
    def __init__(self, folder: str | os.PathLike, system=None, own_pid: int | None = None) -> None:
        self.path = Path(folder) / STATE_FILE
        self.system = system
        self.own_pid = own_pid or os.getpid()
        self.lock = threading.Lock()
        self.state: dict | None = None

    def _sys(self):
        if self.system is None:
            self.system = WindowsSystem()
        return self.system

    def _save(self) -> None:
        try:
            if self.state:
                self.path.write_text(json.dumps(self.state, ensure_ascii=False), encoding="utf-8")
            else:
                self.path.unlink(missing_ok=True)
        except OSError:
            pass

    @property
    def active(self) -> bool:
        return self.state is not None

    def start(self, game: str = "游戏") -> str:
        """开游戏了:游戏优先、后台让路、高性能电源。返回给你看的一句话(什么都没改就是空的)。"""
        with self.lock:
            if self.state is not None:
                return ""
            system = self._sys()
            state = {"game": game, "time": time.time(), "changed": [], "power": None}
            game_pid = system.foreground_pid()
            names = dict(system.processes())
            done = []
            if game_pid and game_pid != self.own_pid:
                old = system.get_priority(game_pid)
                if old in (NORMAL, BELOW_NORMAL, IDLE) and system.set_priority(game_pid, HIGH):
                    state["changed"].append([game_pid, names.get(game_pid, ""), old])
                    done.append(f"{game} 设成高优先级")
            slowed = []
            for pid, exe in names.items():
                base = _base(exe)
                if pid in (game_pid, self.own_pid) or base not in YIELD or base in NEVER:
                    continue
                old = system.get_priority(pid)
                if old in (NORMAL, ABOVE_NORMAL) and system.set_priority(pid, BELOW_NORMAL):
                    state["changed"].append([pid, exe, old])
                    slowed.append(base)
            if slowed:
                unique = sorted(set(slowed))
                done.append(f"{len(unique)} 个后台程序让路({'、'.join(unique[:5])}{'……' if len(unique) > 5 else ''})")
            mine = system.get_priority(self.own_pid)
            if mine in (NORMAL, ABOVE_NORMAL) and system.set_priority(self.own_pid, BELOW_NORMAL):
                state["changed"].append([self.own_pid, "星期五", mine])
            active, plans = system.power_plans()
            fast = fast_plan(plans)
            if active and fast and active != fast[0] and system.set_power_plan(fast[0]):
                state["power"] = active
                done.append(f"电源换成“{fast[1] or '高性能'}”")
            self.state = state
            _gaming.set()
            self._save()
        return ("游戏优化:" + ",".join(done) + ";打完自动改回来。") if done else ""

    def stop(self) -> str:
        """打完了:全部改回原样。"""
        with self.lock:
            state = self.state
            if state is None:
                return ""
            text = self._restore(state)
            self.state = None
            _gaming.clear()
            self._save()
        return text

    def _restore(self, state: dict) -> str:
        system = self._sys()
        names = dict(system.processes())
        back = 0
        for pid, exe, old in state.get("changed") or []:
            if exe != "星期五" and _base(names.get(pid, "")) != _base(exe):
                continue  # 程序已经关了(编号可能换给了别的程序):不动
            try:
                if system.set_priority(int(pid), int(old)):
                    back += 1
            except Exception:
                continue
        if state.get("power"):
            system.set_power_plan(state["power"])
        return f"{state.get('game') or '游戏'}打完了,优化已经改回原样。" if back or state.get("power") else ""

    def recover(self) -> bool:
        """上次打游戏时星期五意外关掉了:把电源计划、优先级改回去。改了返回 True。"""
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        with self.lock:
            try:
                self._restore(state)
            except Exception:
                pass
            self.state = None
            _gaming.clear()
            self._save()
        return True

    def describe(self) -> str:
        if self.state is None:
            return ("现在没在打游戏。开游戏时我会自动让游戏优先、后台程序让路、电源换高性能,打完改回来。"
                    if enabled() else "游戏优化关着(齿轮设定 → 打游戏时 可以打开)。")
        slowed = len([c for c in self.state["changed"] if c[1] != "星期五"])
        return (f"正在为 {self.state['game']} 优化:改了 {slowed} 个程序的优先级"
                + (",电源是高性能" if self.state.get("power") else "") + "。打完会自动改回来。")


# ======================== 游戏体检(只看,不改) ========================


def _reg(root, path: str, name: str):
    try:
        import winreg

        with winreg.OpenKey(root, path) as key:
            return winreg.QueryValueEx(key, name)[0]
    except (OSError, ImportError):
        return None


def checkup(system=None, reg=_reg) -> str:
    """打游戏前看看 Windows 设定有没有拖慢游戏,给建议(不改任何东西)。"""
    try:
        import winreg

        HKCU, HKLM = winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE
    except ImportError:
        HKCU, HKLM = "HKCU", "HKLM"
    tips, good = [], []
    if reg(HKCU, r"Software\Microsoft\GameBar", "AutoGameModeEnabled") == 0:
        tips.append("Windows 的“游戏模式”关着:设置 → 游戏 → 游戏模式,打开它")
    else:
        good.append("Windows 游戏模式开着")
    if reg(HKCU, r"System\GameConfigStore", "GameDVR_Enabled") == 1:
        tips.append("Xbox Game Bar 在后台一直录影(会吃一点帧数):设置 → 游戏 → 屏幕截图/捕获,关掉“录制发生的情况”")
    else:
        good.append("后台录影关着")
    if reg(HKLM, r"SYSTEM\CurrentControlSet\Control\GraphicsDrivers", "HwSchMode") == 2:
        good.append("硬件加速 GPU 计划开着")
    else:
        tips.append("“硬件加速 GPU 计划”没开:设置 → 系统 → 屏幕 → 显示卡 → 默认图形设置,打开后重开电脑"
                    "(NVIDIA RTX 显卡用 DLSS 帧生成一定要开)")
    try:
        system = system or WindowsSystem()
        active, plans = system.power_plans()
        fast = fast_plan(plans)
        if active == POWER_PLANS["saver"]:
            tips.append("电源计划是“节能”:会明显拖慢游戏")
        elif fast and active == fast[0]:
            good.append(f"电源计划是“{fast[1]}”")
        elif fast:
            good.append(f"电源平时是平衡,开游戏时我会自动换成“{fast[1]}”")
    except Exception:
        pass
    text = ("看起来都没问题:" + "、".join(good) + "。") if not tips else (
        "有几个地方可以让游戏更顺:\n" + "\n".join(f"• {t}" for t in tips) + ("\n已经好的:" + "、".join(good) + "。" if good else ""))
    return text
