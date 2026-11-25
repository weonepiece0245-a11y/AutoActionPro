"""给代码审查用的干净副本:只放星期五的程序代码,放在“下载”里,交给审查工具就不会选错文件夹。

不放进去的:钥匙、密码、授权文件、记忆、对话、设定(friday_*.json 这些资料)、浏览器资料(browser-profile)、
自我测试和旧版本的副本(friday_lab、_update_backup)、语音模型、网页用的第三方程序库(web/vendor)。
最多留最近 3 份,更旧的自动删掉(只删这里做出来的副本)。
"""

from __future__ import annotations

import datetime
import shutil
from pathlib import Path
from typing import Callable

CODE_SUFFIXES = {".py", ".md", ".txt", ".bat", ".sh", ".html", ".js", ".css"}
WEB_SKIP = {"vendor"}
PREFIX = "星期五代码审查_"
MARKER = "这是什么.txt"
KEEP = 3


def _never(path: Path, is_secret: Callable[[Path], bool]) -> bool:
    import friday_lab

    return bool(friday_lab.NEVER_COPY.match(path.name)) or path.name.startswith(".") or is_secret(path)


def make(app_dir: str | Path, downloads: str | Path, is_secret: Callable[[Path], bool] = lambda p: False,
         clock: Callable[[], datetime.datetime] = datetime.datetime.now) -> dict:
    """做一份副本。返回 {"path", "files", "bytes"}。"""
    app = Path(app_dir)
    target = Path(downloads) / f"{PREFIX}{clock():%Y%m%d-%H%M%S}"
    target.mkdir(parents=True)
    files = size = 0
    for path in sorted(app.iterdir()):
        if path.is_file() and path.suffix.lower() in CODE_SUFFIXES and not _never(path, is_secret):
            shutil.copy2(path, target / path.name)
            files += 1
            size += path.stat().st_size
    web = app / "web"
    if web.is_dir():
        for path in sorted(web.rglob("*")):
            rel = path.relative_to(web)
            if not path.is_file() or rel.parts[0] in WEB_SKIP or _never(path, is_secret):
                continue
            (target / "web" / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target / "web" / rel)
            files += 1
            size += path.stat().st_size
    (target / MARKER).write_text(
        "这是星期五程序的干净副本,只有代码,给代码审查用。\n"
        "里面没有钥匙、密码、记忆、对话、浏览器资料,也没有旧版本的副本。\n"
        f"做的时间:{clock():%Y-%m-%d %H:%M}\n"
        f"真正的星期五在:{app}\n"
        "审查完可以直接删掉这个文件夹;改这里的代码不会改到真正的星期五。\n", encoding="utf-8")
    _prune(Path(downloads))
    return {"path": str(target), "files": files, "bytes": size}


def _prune(downloads: Path) -> None:
    """只留最近 KEEP 份;只删有说明文件(这里做出来的)的。"""
    copies = sorted((p for p in downloads.glob(PREFIX + "*") if p.is_dir() and (p / MARKER).is_file()),
                    key=lambda p: p.name, reverse=True)
    for old in copies[KEEP:]:
        shutil.rmtree(old, ignore_errors=True)


def latest(downloads: str | Path) -> str:
    copies = sorted((p for p in Path(downloads).glob(PREFIX + "*") if p.is_dir() and (p / MARKER).is_file()),
                    key=lambda p: p.name, reverse=True)
    return str(copies[0]) if copies else ""
