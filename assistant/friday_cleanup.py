"""大文件和重复文件清理:在你的个人文件夹(下载、桌面、文档、视频、图片、音乐)里找出最占空间的文件和一模一样的文件。

只列出来给你看,你勾了才会丢进回收站(还能从回收站拿回来),不会直接删除。
不碰系统文件夹、程序文件夹、星期五自己的文件和钥匙文件。不问 Claude,不花钱。
"""

from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path

BIG_FILE = 100 * 1024 * 1024  # 100MB 以上算大文件
DUP_MIN = 1 * 1024 * 1024  # 1MB 以上才找重复(太小的不值得)
MAX_FILES = 200_000
TIME_LIMIT = 90  # 秒
MAX_HASH_BYTES = 20 * 1024 ** 3  # 最多读 20GB 来比对重复
TOP_BIG = 30
SKIP_DIRS = {"appdata", "node_modules", ".git", "$recycle.bin", "system volume information", "windows",
             "program files", "program files (x86)", "programdata", "__pycache__", ".venv", "venv"}
ASSISTANT_DIR = Path(os.path.dirname(os.path.abspath(__file__)))


def _skip(path: Path) -> bool:
    import friday_security

    try:
        path.resolve().relative_to(ASSISTANT_DIR.parent.resolve())
        return True  # 星期五自己的文件
    except ValueError:
        pass
    return friday_security.is_secret_path(path)


def _hash(path: Path, limit: int | None = None) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        remaining = limit
        while True:
            chunk = f.read(1024 * 1024 if remaining is None else min(1024 * 1024, remaining))
            if not chunk:
                break
            digest.update(chunk)
            if remaining is not None:
                remaining -= len(chunk)
                if remaining <= 0:
                    break
    return digest.hexdigest()


def scan(roots: list[Path], clock=time.monotonic) -> dict:
    """-> {"big": [{path, size, modified}], "dups": [{size, paths}], "files": n, "partial": bool}"""
    started = clock()
    files: list[tuple[Path, int, float]] = []
    seen: set = set()  # 同一个文件只算一次(几个文件夹重叠时,不会变成“跟自己重复”,把唯一那份当成多余的)
    partial = False
    for root in roots:
        if not Path(root).is_dir():
            continue
        for folder, dirs, names in os.walk(root):
            dirs[:] = [d for d in dirs if d.lower() not in SKIP_DIRS and not d.startswith(".")]
            for name in names:
                path = Path(folder) / name
                try:
                    if path.is_symlink():
                        continue
                    stat = path.stat()
                except OSError:
                    continue
                key = (stat.st_dev, stat.st_ino) if stat.st_ino else os.path.normcase(os.path.realpath(path))
                if key in seen:
                    continue
                seen.add(key)
                files.append((path, stat.st_size, stat.st_mtime))
                if len(files) >= MAX_FILES or clock() - started > TIME_LIMIT:
                    partial = True
                    break
            if partial:
                break
        if partial:
            break
    files = [f for f in files if not _skip(f[0])]
    big = sorted((f for f in files if f[1] >= BIG_FILE), key=lambda f: -f[1])[:TOP_BIG]
    by_size: dict[int, list[Path]] = {}
    for path, size, _ in files:
        if size >= DUP_MIN:
            by_size.setdefault(size, []).append(path)
    dups, hashed = [], 0
    for size, paths in sorted(by_size.items(), key=lambda kv: -kv[0]):
        if len(paths) < 2:
            continue
        if hashed > MAX_HASH_BYTES or clock() - started > TIME_LIMIT * 2:
            partial = True
            break
        groups: dict[str, list[Path]] = {}
        for path in paths:  # 先比开头 64KB,一样的再比整个文件
            try:
                groups.setdefault(_hash(path, 64 * 1024), []).append(path)
            except OSError:
                continue
        for same_start in groups.values():
            if len(same_start) < 2:
                continue
            full: dict[str, list[Path]] = {}
            for path in same_start:
                try:
                    full.setdefault(_hash(path), []).append(path)
                    hashed += size
                except OSError:
                    continue
            for same in full.values():
                if len(same) >= 2:
                    dups.append({"size": size, "paths": [str(p) for p in sorted(same, key=lambda p: len(str(p)))]})
    return {"big": [{"path": str(p), "size": s, "modified": m} for p, s, m in big], "dups": dups[:50],
            "files": len(files), "partial": partial}


def size_text(size: float) -> str:
    gb = size / 1024 ** 3
    return f"{gb:.1f}GB" if gb >= 1 else f"{size / 1024 ** 2:.0f}MB"


def describe(result: dict) -> str:
    big, dups = result["big"], result["dups"]
    if not big and not dups:
        return f"看了 {result['files']} 个文件,没有特别大的,也没有重复的。"
    lines = [f"看了 {result['files']} 个文件" + (",时间太长只看了一部分" if result.get("partial") else "") + "。"]
    if big:
        lines.append(f"最大的 {min(5, len(big))} 个:" + "、".join(
            f"{Path(b['path']).name}({size_text(b['size'])})" for b in big[:5]) + "。")
    if dups:
        wasted = sum(d["size"] * (len(d["paths"]) - 1) for d in dups)
        lines.append(f"有 {len(dups)} 组一模一样的文件,删掉多出来的能省 {size_text(wasted)}。")
    lines.append("在齿轮设定 → 电脑健康 → 大文件和重复文件 里勾选要丢掉的,会放进回收站,还能拿回来。")
    return "".join(lines)


def candidates(result: dict) -> list[dict]:
    """可以勾选丢掉的:大文件,和每组重复里“多出来的”那几个(第一个留着)。"""
    items, seen = [], set()
    for d in result.get("dups", []):
        for path in d["paths"][1:]:
            if path not in seen:
                seen.add(path)
                items.append({"path": path, "size": d["size"], "kind": "dup", "keep": d["paths"][0]})
    for b in result.get("big", []):
        if b["path"] not in seen:
            seen.add(b["path"])
            items.append({"path": b["path"], "size": b["size"], "kind": "big"})
    for n, item in enumerate(items, 1):
        item["id"] = n
    return items
