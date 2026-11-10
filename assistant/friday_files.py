"""找文件:按名字里的字、文件类型、最近几天改过、在哪个文件夹,在你的电脑里找出来(不花钱)。

找的地方:你的下载、桌面、文档、图片、视频、音乐、OneDrive,还有 C 盘以外的硬盘(D 盘、E 盘……)。
不找系统和程序文件夹;星期五自己的文件和钥匙、密码文件不列出来,也不打开。
"""

from __future__ import annotations

import os
import string
import time
from pathlib import Path

KINDS = {
    "pdf": ({".pdf"}, "PDF"),
    "word": ({".doc", ".docx", ".rtf", ".odt", ".wps"}, "Word 文件"),
    "excel": ({".xls", ".xlsx", ".xlsm", ".csv", ".ods", ".et"}, "Excel 表格"),
    "ppt": ({".ppt", ".pptx", ".odp", ".dps"}, "PPT"),
    "image": ({".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".heic", ".tif", ".tiff", ".svg"}, "图片"),
    "video": ({".mp4", ".mov", ".mkv", ".avi", ".wmv", ".m4v", ".webm", ".3gp"}, "视频"),
    "music": ({".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".wma"}, "音乐"),
    "zip": ({".zip", ".rar", ".7z", ".tar", ".gz"}, "压缩包"),
    "text": ({".txt", ".md", ".log", ".json"}, "文字文件"),
    "installer": ({".exe", ".msi"}, "安装程序"),
}
# 这些打开会执行程序,星期五不替你打开
NO_OPEN = {".exe", ".msi", ".bat", ".cmd", ".ps1", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh", ".scr", ".com",
           ".lnk", ".url", ".reg", ".hta", ".cpl", ".msc", ".jar", ".py", ".pyw"}
SKIP_DIRS = {"appdata", "node_modules", ".git", "$recycle.bin", "system volume information", "windows",
             "program files", "program files (x86)", "programdata", "__pycache__", ".venv", "venv",
             "site-packages", "steamapps", "windowsapps", "$windows.~bt", "$windows.~ws", "recovery", "msocache"}
MAX_ENTRIES = 400_000
TIME_LIMIT = 25.0  # 秒
FOLDER_WORDS = {"下载": "downloads", "桌面": "desktop", "文档": "documents", "图片": "pictures", "照片": "pictures",
                "视频": "videos", "音乐": "music"}


def other_drives() -> list[Path]:
    """C 盘以外的硬盘(只在 Windows 上)。"""
    if os.name != "nt":
        return []
    system = os.environ.get("SystemDrive", "C:").upper().rstrip("\\")
    try:
        drives = [Path(d) for d in os.listdrives()]  # Python 3.12+
    except AttributeError:
        drives = [Path(f"{c}:\\") for c in string.ascii_uppercase if os.path.exists(f"{c}:\\")]
    found = []
    for d in drives:
        if str(d).upper().rstrip("\\") == system:
            continue
        try:
            import ctypes

            if ctypes.windll.kernel32.GetDriveTypeW(str(d)) not in (2, 3):  # 只要本机硬盘和 U 盘,不找光驱和网络盘
                continue
        except Exception:
            pass
        found.append(d)
    return found


def _hidden_or_skipped(name: str) -> bool:
    low = name.lower()
    return low in SKIP_DIRS or name.startswith((".", "$"))


def search(roots: list[Path], words: list[str] | None = None, kind: str | None = None, days: float | None = None,
           limit: int = 20, now=time.time, clock=time.monotonic, skip=None) -> dict:
    """-> {"files": [{"path","size","modified"}](新的在前), "total": 找到几个, "partial": 有没有找完}"""
    words = [w.lower() for w in (words or []) if w and w.strip()]
    exts = KINDS[kind][0] if kind in KINDS else None
    since = now() - days * 86400 if days else None
    started = clock()
    seen, hits, partial = 0, [], False
    done_roots: list[Path] = []
    for root in roots:
        root = Path(root)
        if not root.is_dir() or any(_inside(root, r) for r in done_roots):
            continue
        done_roots.append(root)
        for folder, dirs, names in os.walk(root):
            dirs[:] = [d for d in dirs if not _hidden_or_skipped(d)]
            for name in names:
                seen += 1
                low = name.lower()
                if exts is not None and os.path.splitext(low)[1] not in exts:
                    continue
                if words and not all(w in low for w in words):
                    continue
                path = Path(folder) / name
                try:
                    stat = path.stat()
                except OSError:
                    continue
                if since is not None and stat.st_mtime < since:
                    continue
                if skip is not None and skip(path):
                    continue
                hits.append({"path": str(path), "size": stat.st_size, "modified": stat.st_mtime})
            if seen >= MAX_ENTRIES or clock() - started > TIME_LIMIT:
                partial = True
                break
        if partial:
            break
    hits.sort(key=lambda h: -h["modified"])
    return {"files": hits[:limit], "total": len(hits), "partial": partial}


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except (ValueError, OSError):
        return False


def size_text(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f}{unit}" if unit in ("B", "KB") else f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}GB"


def when_text(stamp: float, now: float | None = None) -> str:
    import datetime

    moment = datetime.datetime.fromtimestamp(stamp)
    today = datetime.datetime.fromtimestamp(now or time.time()).date()
    days = (today - moment.date()).days
    if days == 0:
        return f"今天 {moment:%H:%M}"
    if days == 1:
        return f"昨天 {moment:%H:%M}"
    return f"{moment.month}/{moment.day}" if moment.year == today.year else f"{moment:%Y/%m/%d}"


def describe(result: dict, numbered: bool = True) -> str:
    files = result["files"]
    if not files:
        return "没找到这样的文件。" + ("(电脑里文件太多,只找了一部分)" if result["partial"] else "")
    head = f"找到 {result['total']} 个" + (f",这是最新的 {len(files)} 个" if result["total"] > len(files) else "") + ":"
    lines = [f"{('#' + str(i + 1) + ' ') if numbered else ''}{Path(f['path']).name}({when_text(f['modified'])},"
             f"{size_text(f['size'])})\n   {f['path']}" for i, f in enumerate(files)]
    tail = "\n(电脑里文件太多,只找了一部分)" if result["partial"] else ""
    return head + "\n" + "\n".join(lines) + tail


def can_open(path: str | Path) -> bool:
    return Path(path).suffix.lower() not in NO_OPEN
