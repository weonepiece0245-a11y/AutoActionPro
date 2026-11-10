"""照片按月整理:把文件夹最上层的照片和视频,按拍摄日期分进“2026-11”这样的子文件夹(不花钱)。

拍摄日期先看照片里记录的时间(EXIF),没有就看文件名(IMG_20261110_…、Screenshot_2026-11-10…),再没有就用文件修改时间。
10 分钟内刚改过的不动;重名不覆盖;整理错了可以撤销。
"""

from __future__ import annotations

import datetime
import json
import os
import re
import time
from pathlib import Path

PHOTO_SUFFIXES = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".dng",
                  ".mp4", ".mov", ".m4v", ".3gp", ".avi"}
RECENT_SECONDS = 10 * 60
LOG_NAME = "friday_photo_sort.json"
ASSISTANT_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
NAME_DATE = re.compile(r"(20\d{2})[-_.]?(0[1-9]|1[0-2])[-_.]?(0[1-9]|[12]\d|3[01])")


def taken_month(path: Path) -> str:
    """照片是哪年哪月拍的:'2026-11'。"""
    try:
        from PIL import Image

        with Image.open(path) as image:
            exif = image.getexif()
            raw = exif.get_ifd(0x8769).get(36867) or exif.get(306)  # 拍摄时间 / 修改时间
        if raw:
            found = re.match(r"(\d{4}):(\d{2})", str(raw))
            if found and found.group(1) != "0000":
                return f"{found.group(1)}-{found.group(2)}"
    except Exception:
        pass
    found = NAME_DATE.search(path.name)
    if found:
        return f"{found.group(1)}-{found.group(2)}"
    return datetime.datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m")


def _free_name(target: Path) -> Path:
    if not target.exists():
        return target
    for n in range(1, 1000):
        candidate = target.with_name(f"{target.stem} ({n}){target.suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"{target.name} 重名太多")


def check_folder(folder: Path) -> Path:
    folder = Path(os.path.expandvars(os.path.expanduser(str(folder)))).resolve()
    if not folder.is_dir():
        raise ValueError(f"找不到文件夹 {folder}")
    if folder == Path(folder.anchor) or folder == Path.home().resolve():
        raise ValueError("不能整理整个硬盘或用户文件夹,请选一个放照片的文件夹")
    try:
        folder.relative_to(ASSISTANT_DIR.parent.resolve())
        inside_friday = True
    except ValueError:
        inside_friday = False
    if inside_friday:
        raise ValueError("这是星期五自己的文件夹,不能整理")
    lowered = str(folder).lower()
    if any(part in lowered for part in ("\\windows", "\\program files", "\\appdata", "/usr/", "/etc/")):
        raise ValueError("这是系统或程序的文件夹,不能整理")
    return folder


def sort_photos(folder: Path, log_dir: Path = ASSISTANT_DIR, now=time.time) -> dict:
    """-> {"moved": n, "months": {"2026-11": 3, ...}};记录下来可以撤销。"""
    folder = check_folder(folder)
    moves, months = [], {}
    for item in sorted(folder.iterdir()):
        if not item.is_file() or item.suffix.lower() not in PHOTO_SUFFIXES or item.name.startswith("."):
            continue
        try:
            if now() - item.stat().st_mtime < RECENT_SECONDS:
                continue
            month = taken_month(item)
        except OSError:
            continue
        target = _free_name(folder / month / item.name)
        target.parent.mkdir(exist_ok=True)
        try:
            os.replace(item, target)
        except OSError:
            continue
        moves.append([str(item), str(target)])
        months[month] = months.get(month, 0) + 1
    if moves:
        (Path(log_dir) / LOG_NAME).write_text(json.dumps({"folder": str(folder), "moves": moves}, ensure_ascii=False),
                                              encoding="utf-8")
    return {"moved": len(moves), "months": months, "folder": str(folder)}


def undo(log_dir: Path = ASSISTANT_DIR) -> int:
    path = Path(log_dir) / LOG_NAME
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    back, folders = 0, set()
    for original, moved in reversed(record.get("moves", [])):
        src, dst = Path(moved), Path(original)
        if src.exists() and not dst.exists():
            try:
                os.replace(src, dst)
                back += 1
                folders.add(src.parent)
            except OSError:
                pass
    for sub in folders:
        try:
            sub.rmdir()  # 空了就删掉
        except OSError:
            pass
    path.unlink(missing_ok=True)
    return back


def describe(result: dict) -> str:
    if not result["moved"]:
        return "这个文件夹最上层没有要整理的照片(10 分钟内刚放进来的先不动)。"
    months = sorted(result["months"].items())
    parts = "、".join(f"{m} {n} 张" for m, n in months[:8]) + ("……" if len(months) > 8 else "")
    return f"整理好了:{result['moved']} 张照片和视频按月分进了 {len(months)} 个文件夹({parts})。整理错了可以撤销。"
