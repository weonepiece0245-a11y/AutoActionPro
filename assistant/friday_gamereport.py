"""游戏后报告:每局打完告诉你打了多久、星期五自己用了多少电脑、电脑最热几度、哪个后台程序比较忙(不花钱)。

打游戏时每分钟看一次:每个程序用了多少 CPU(和任务管理器一样的算法)、星期五自己的内存、CPU 和显卡温度。
打完(离开游戏 90 秒)说一次;5 分钟以内的不报。问“刚才那局电脑怎么样”“游戏报告”可以再听一次。
只看不改,也不记录你在游戏里做什么。
"""

from __future__ import annotations

import os
import threading
import time
from typing import Callable

MIN_SECONDS = 5 * 60
BUSY_PERCENT = 3.0  # 平均用到整台电脑 3% 以上才算“比较忙”
ANTI_CHEAT = {"easyanticheat", "easyanticheat_eos", "beservice", "vgc", "vgtray", "faceitclient", "faceit",
              "eac_launcher", "battleye", "gameguard", "xigncode", "ace-base", "acetutor"}
SKIP = {"system idle process", "[system process]", "idle", "memory compression", "registry", "secure system"}
FRIENDLY = {
    "audiodg": "Windows 音频处理(偏高通常是音效软件,比如 Razer THX、音频增强)",
    "dwm": "桌面窗口管理器(画画面的,正常)",
    "msmpeng": "Windows Defender 病毒扫描",
    "searchindexer": "Windows 搜索在建索引",
    "msedge": "Edge 浏览器(也是星期五的窗口)",
    "chrome": "Chrome 浏览器",
    "discord": "Discord",
    "steamwebhelper": "Steam 网页/叠加层",
    "onedrive": "OneDrive 同步",
    "nvcontainer": "NVIDIA 程序",
    "obs64": "OBS 录像",
    "tiworker": "Windows 更新",
    "trustedinstaller": "Windows 更新",
    "mousocoreworker": "Windows 更新",
    "compattelrunner": "Windows 遥测",
}


def _base(name: str) -> str:
    name = str(name or "").lower()
    return name[:-4] if name.endswith(".exe") else name


def duration_text(seconds: float) -> str:
    minutes = int(seconds // 60)
    hours, rest = divmod(minutes, 60)
    return f"{hours} 小时 {rest} 分" if hours else f"{minutes} 分钟"


# ======================== Windows ========================


class WindowsSampler:
    """读每个程序到现在一共用了多少 CPU 时间、星期五自己的内存。"""

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        import friday_gameboost

        self.ctypes, self.wintypes = ctypes, wintypes
        self.system = friday_gameboost.WindowsSystem()
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.OpenProcess.restype = wintypes.HANDLE
        self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.cpus = os.cpu_count() or 1

    def cpu_times(self, skip: set[str] = frozenset()) -> dict[int, tuple[str, float]]:
        """{pid: (程序名, 一共用了几秒 CPU)}。skip 里的程序(游戏本身、防作弊)不去打开。"""
        ctypes, wintypes = self.ctypes, self.wintypes
        found = {}
        for pid, name in self.system.processes():
            if not pid or _base(name) in skip:
                continue
            handle = self.kernel.OpenProcess(0x1000, False, pid)
            if not handle:
                continue
            try:
                times = [wintypes.FILETIME() for _ in range(4)]
                if self.kernel.GetProcessTimes(handle, *[ctypes.byref(t) for t in times]):
                    kernel, user = times[2], times[3]
                    total = ((kernel.dwHighDateTime << 32) | kernel.dwLowDateTime) + \
                            ((user.dwHighDateTime << 32) | user.dwLowDateTime)
                    found[pid] = (name, total / 10_000_000)
            finally:
                self.kernel.CloseHandle(handle)
        return found

    def own_memory(self) -> float:
        """星期五自己用了多少内存(MB)。"""
        ctypes, wintypes = self.ctypes, self.wintypes

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        process = ctypes.windll.kernel32.GetCurrentProcess()
        if not ctypes.windll.psapi.GetProcessMemoryInfo(process, ctypes.byref(counters), counters.cb):
            return 0.0
        return counters.WorkingSetSize / 1024 ** 2


# ======================== 报告 ========================


class GameReport:
    def __init__(self, sampler=None, temps: Callable[[], dict] | None = None, clock: Callable[[], float] = time.time,
                 own_pid: int | None = None, cpus: int | None = None) -> None:
        self.sampler = sampler
        self.temps = temps
        self.clock = clock
        self.own_pid = own_pid or os.getpid()
        self.cpus = cpus
        self.lock = threading.Lock()
        self.active: dict | None = None
        self.last_text = ""

    def _sam(self):
        if self.sampler is None:
            self.sampler = WindowsSampler()
        return self.sampler

    def start(self, game: str, game_exe: str = "") -> None:
        with self.lock:
            self.active = {"game": game, "game_exe": _base(game_exe), "start": self.clock(), "prev": None,
                           "prev_at": None, "usage": {}, "samples": 0, "own_cpu": 0.0, "own_mem": 0.0,
                           "cpu_temp": None, "gpu_temp": None}
        self.sample()

    def sample(self) -> None:
        """打游戏时每分钟调用一次。"""
        with self.lock:
            state = self.active
            if state is None:
                return
            sampler = self._sam()
            now = self.clock()
            skip = set(ANTI_CHEAT) | ({state["game_exe"]} if state["game_exe"] else set())
            try:
                times = sampler.cpu_times(skip=skip)
            except Exception:
                times = {}
            cpus = self.cpus or getattr(sampler, "cpus", None) or os.cpu_count() or 1
            prev, prev_at = state["prev"], state["prev_at"]
            if prev is not None and now > prev_at:
                wall = (now - prev_at) * cpus
                state["samples"] += 1
                for pid, (name, total) in times.items():
                    if pid not in prev or prev[pid][0] != name:
                        continue
                    percent = max(0.0, (total - prev[pid][1]) / wall * 100)
                    if pid == self.own_pid:
                        state["own_cpu"] = max(state["own_cpu"], percent)
                        continue
                    base = _base(name)
                    if base in SKIP or base == state["game_exe"]:
                        continue
                    state["usage"][base] = state["usage"].get(base, 0.0) + percent
            state["prev"], state["prev_at"] = times, now
            try:
                state["own_mem"] = max(state["own_mem"], float(sampler.own_memory()))
            except Exception:
                pass
            if self.temps is not None:
                try:
                    t = self.temps()
                    for key in ("cpu", "gpu"):
                        if t.get(key) is not None:
                            state[f"{key}_temp"] = max(state[f"{key}_temp"] or 0, t[key])
                except Exception:
                    pass

    def stop(self) -> str:
        """打完了:返回报告(太短的局返回空)。"""
        with self.lock:
            state, self.active = self.active, None
        if state is None:
            return ""
        played = self.clock() - state["start"]
        if played < MIN_SECONDS:
            return ""
        text = self._compose(state, played)
        self.last_text = text
        return text

    @staticmethod
    def _compose(state: dict, played: float) -> str:
        parts = [f"{state['game']} 打了 {duration_text(played)}。"]
        mine = []
        if state["samples"]:
            mine.append(f"CPU 最多 {state['own_cpu']:.1f}%")
        if state["own_mem"]:
            mine.append(f"内存 {state['own_mem']:.0f} MB")
        if mine:
            light = state["own_cpu"] < 3
            parts.append("星期五自己:" + "、".join(mine) + ("(很轻,没跟游戏抢)。" if light else "。"))
        temps = []
        if state["cpu_temp"]:
            temps.append(f"CPU 最高 {state['cpu_temp']:.0f}°C")
        if state["gpu_temp"]:
            temps.append(f"显卡最高 {state['gpu_temp']:.0f}°C")
        if temps:
            parts.append("温度:" + "、".join(temps) + "。")
        samples = max(state["samples"], 1)
        busy = sorted(((total / samples, name) for name, total in state["usage"].items()), reverse=True)
        busy = [(avg, name) for avg, name in busy if avg >= BUSY_PERCENT][:3]
        if busy:
            parts.append("后台比较忙的:" + ";".join(f"{FRIENDLY.get(name, name)} 平均 {avg:.0f}%" for avg, name in busy) + "。")
        elif state["samples"]:
            parts.append("后台程序都很安静。")
        return "".join(parts)

    def describe(self) -> str:
        if self.active is not None:
            return f"正在记录 {self.active['game']} 这一局,打完告诉你。"
        return self.last_text or "还没有游戏报告。打完一局(5 分钟以上)我会告诉你。"
