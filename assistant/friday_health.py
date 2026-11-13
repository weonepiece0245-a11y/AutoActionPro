"""电脑健康:硬盘快满、内存一直很紧、很多天没重启、下载文件夹太乱,星期五主动告诉你(不问 Claude,不花钱)。

还能一键整理下载文件夹:按类型分进“图片”“文档”“安装程序”……几个文件夹;整理错了可以撤销。
"""

from __future__ import annotations

import datetime
import json
import os
import shutil
import time
from pathlib import Path
from typing import Callable

DISK_LOW_PERCENT = 10  # 剩余不到 10%
DISK_LOW_GB = 15  # 或者不到 15GB
MEMORY_HIGH_PERCENT = 90  # 内存用了九成以上
MEMORY_HIGH_CHECKS = 3  # 连续这么多次(每次隔 5 分钟)才算“一直很紧”
UPTIME_DAYS = 7
DOWNLOADS_MANY_FILES = 150
DOWNLOADS_BIG_GB = 10

TIDY_GROUPS = {
    "图片": {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".heic", ".svg", ".tif", ".tiff", ".ico"},
    "视频": {".mp4", ".mkv", ".mov", ".avi", ".wmv", ".webm", ".flv", ".m4v"},
    "音乐": {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".wma", ".opus"},
    "文档": {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".csv", ".md", ".rtf", ".odt", ".epub"},
    "压缩包": {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".iso"},
    "安装程序": {".exe", ".msi", ".msix", ".appx", ".apk"},
}
SKIP_SUFFIXES = {".crdownload", ".part", ".tmp", ".partial", ".download", ".opdownload", ".!ut", ".ini", ".lnk"}
RECENT_SECONDS = 10 * 60  # 10 分钟内改过的(可能还在下载)不动
TIDY_LOG = "friday_tidy.json"
ASSISTANT_DIR = Path(os.path.dirname(os.path.abspath(__file__)))


# ======================== 读电脑状态 ========================


def memory_percent() -> float | None:
    if os.name != "nt":
        return None
    try:
        import ctypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

        status = MEMORYSTATUSEX()
        status.dwLength = ctypes.sizeof(status)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return None
        return float(status.dwMemoryLoad)
    except Exception:
        return None


def uptime_days() -> float | None:
    if os.name != "nt":
        return None
    try:
        import ctypes

        ctypes.windll.kernel32.GetTickCount64.restype = ctypes.c_ulonglong
        return ctypes.windll.kernel32.GetTickCount64() / 1000 / 86400
    except Exception:
        return None


def drives() -> list[str]:
    if os.name != "nt":
        return ["/"]
    try:
        import ctypes

        mask = ctypes.windll.kernel32.GetLogicalDrives()
        found = []
        for i in range(26):
            if mask & (1 << i):
                root = f"{chr(65 + i)}:\\"
                if ctypes.windll.kernel32.GetDriveTypeW(root) == 3:  # 本机硬盘
                    found.append(root)
        return found
    except Exception:
        return ["C:\\"]


def disk_report(roots: list[str] | None = None) -> list[dict]:
    out = []
    for root in roots if roots is not None else drives():
        try:
            usage = shutil.disk_usage(root)
        except OSError:
            continue
        free_gb = usage.free / 1024 ** 3
        percent = usage.free / usage.total * 100 if usage.total else 100
        out.append({"drive": root.rstrip("\\/") or root, "free_gb": round(free_gb, 1),
                    "total_gb": round(usage.total / 1024 ** 3), "free_percent": round(percent, 1),
                    "low": percent < DISK_LOW_PERCENT or free_gb < DISK_LOW_GB})
    return out


def downloads_report(folder: Path) -> dict | None:
    try:
        entries = list(Path(folder).iterdir())
    except OSError:
        return None
    files = [e for e in entries if e.is_file()]
    size = 0
    for f in files:
        try:
            size += f.stat().st_size
        except OSError:
            pass
    return {"folder": str(folder), "files": len(files), "gb": round(size / 1024 ** 3, 1),
            "messy": len(files) >= DOWNLOADS_MANY_FILES or size / 1024 ** 3 >= DOWNLOADS_BIG_GB}


# ======================== 整理下载文件夹 ========================


def _free_name(target: Path) -> Path:
    if not target.exists():
        return target
    for n in range(1, 1000):
        candidate = target.with_name(f"{target.stem} ({n}){target.suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"{target.name} 重名太多")


def tidy(folder: Path, log_dir: Path = ASSISTANT_DIR, now: Callable[[], float] = time.time) -> dict:
    """把下载文件夹最上层的文件按类型分进子文件夹。返回 {moved: n, groups: {类型: n}};记录下来可以撤销。"""
    folder = Path(folder)
    moves, groups = [], {}
    for item in sorted(folder.iterdir()):
        if not item.is_file() or item.name.startswith(".") or item.suffix.lower() in SKIP_SUFFIXES:
            continue
        try:
            if now() - item.stat().st_mtime < RECENT_SECONDS:
                continue  # 可能还在下载
        except OSError:
            continue
        group = next((g for g, exts in TIDY_GROUPS.items() if item.suffix.lower() in exts), "其他")
        target = _free_name(folder / group / item.name)
        target.parent.mkdir(exist_ok=True)
        try:
            os.replace(item, target)
        except OSError:
            continue  # 正在被别的程序用
        moves.append([str(item), str(target)])
        groups[group] = groups.get(group, 0) + 1
    if moves:
        record = {"time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), "folder": str(folder), "moves": moves}
        (Path(log_dir) / TIDY_LOG).write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    return {"moved": len(moves), "groups": groups}


def undo_tidy(log_dir: Path = ASSISTANT_DIR) -> int:
    """把上次整理挪走的文件放回原处。返回放回了几个。"""
    path = Path(log_dir) / TIDY_LOG
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    back = 0
    for original, moved in reversed(record.get("moves", [])):
        src, dst = Path(moved), Path(original)
        if src.exists() and not dst.exists():
            try:
                os.replace(src, dst)
                back += 1
            except OSError:
                pass
    for group in TIDY_GROUPS.keys() | {"其他"}:
        sub = Path(record.get("folder", "")) / group
        try:
            sub.rmdir()  # 空了就删掉
        except OSError:
            pass
    path.unlink(missing_ok=True)
    return back


def describe_tidy(result: dict) -> str:
    if not result["moved"]:
        return "下载文件夹里没有要整理的文件(最近 10 分钟内下载的先不动)。"
    parts = "、".join(f"{g} {n} 个" for g, n in sorted(result["groups"].items(), key=lambda kv: -kv[1]))
    return f"整理好了:{result['moved']} 个文件分进了子文件夹({parts})。整理错了可以说“撤销整理”。"


# ======================== 检查 ========================


class HealthCheck:
    """每 5 分钟看一次;有问题返回要说的话(同一类问题一天只说一次)。"""

    def __init__(self, state, downloads: Callable[[], Path], memory=memory_percent, uptime=uptime_days,
                 disks: Callable[[], list[dict]] = disk_report, top: Callable[[], list[dict]] | None = None,
                 today: Callable[[], str] = lambda: datetime.date.today().isoformat()) -> None:
        self.state = state  # friday_alerts.AlertState
        self.downloads = downloads
        self.memory = memory
        self.uptime = uptime
        self.disks = disks
        self.top = top  # 最占内存的程序(内存紧时说出来)
        self.today = today
        self.memory_streak = 0

    def _once(self, kind: str) -> bool:
        key = f"health_{kind}"
        if self.state.get(key) == self.today():
            return False
        self.state.set(key, self.today())
        return True

    def check(self, name: str) -> list[str]:
        if os.environ.get("FRIDAY_HEALTH", "on") == "off":
            return []
        said = []
        for disk in self.disks():
            if disk["low"] and self._once(f"disk_{disk['drive']}"):
                said.append(f"{name},{disk['drive']} 盘只剩 {disk['free_gb']}GB({disk['free_percent']:.0f}%),快满了。"
                            "可以清一下回收站,或者在齿轮设定 → 电脑健康 → 大文件和重复文件 点“找一找”,看看哪些能删。")
        percent = self.memory()
        self.memory_streak = self.memory_streak + 1 if percent is not None and percent >= MEMORY_HIGH_PERCENT else 0
        if self.memory_streak >= MEMORY_HIGH_CHECKS and self._once("memory"):
            text = f"{name},内存已经连续一阵子用了 {percent:.0f}% 以上,电脑可能会变慢。"
            try:
                heavy = (self.top() if self.top else [])[:3]
            except Exception:
                heavy = []
            if heavy:
                text += "最占内存的是 " + "、".join(f"{p['name']}({p['mb']}MB)" for p in heavy) + ",用不到的可以关掉。"
            said.append(text)
        days = self.uptime()
        if days is not None and days >= UPTIME_DAYS and self._once("uptime"):
            said.append(f"{name},电脑已经 {int(days)} 天没重启了。有空重启一下,系统更新能装上,也会顺一点。")
        try:
            report = downloads_report(self.downloads())
        except Exception:
            report = None
        if report and report["messy"] and self._once("downloads"):
            said.append(f"{name},下载文件夹里堆了 {report['files']} 个文件(共 {report['gb']}GB)。"
                        "要我按类型整理一下吗?说“整理下载文件夹”就行,整理错了也能撤销。")
        return said


def status(downloads: Path | None) -> dict:
    """给齿轮设定显示的现在状态。"""
    report = downloads_report(downloads) if downloads else None
    days = uptime_days()
    percent = memory_percent()
    return {"disks": disk_report(), "memory": percent, "uptime_days": round(days, 1) if days is not None else None,
            "downloads": report, "enabled": os.environ.get("FRIDAY_HEALTH", "on") != "off"}
