"""重要文件夹自动备份:每天把你选的文件夹复制到另一个硬盘或移动硬盘,只复制新的和改过的(不花钱)。

- 备份里的文件不会因为你删了原文件就被删掉。
- 文件改过的话,备份里旧的那一份移到“_旧版本/日期”里,保留 30 天。
- 一下子很多文件同时变了(像中了勒索病毒),这次就不备份,先提醒你,免得把好的备份盖掉。
- 移动硬盘没插上就等下次,太久没备份会提醒你。
设定存在 friday_folderbackup.json。
"""

from __future__ import annotations

import datetime
import json
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Callable

ASSISTANT_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {"$recycle.bin", "system volume information", "node_modules", ".git", "__pycache__", ".venv", "venv"}
OLD_DIR = "_旧版本"
KEEP_OLD_DAYS = 30
RUN_FROM_HOUR = 10  # 每天 10 点后第一次检查时备份
REMIND_AFTER_DAYS = 7
SUSPICIOUS = (".locked", ".encrypted", ".enc", ".crypt", ".crypted", ".wncry", ".wcry", ".locky", ".cerber",
              ".zepto", ".ryk", ".lockbit", ".akira", ".ransom")


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def drive_present(dest: Path) -> bool:
    """备份的硬盘有没有插上(移动硬盘拔掉时,盘符就不在了)。"""
    return Path(dest.anchor).exists()


def check_source(folder: str) -> Path:
    path = Path(os.path.expandvars(os.path.expanduser(folder.strip().strip('"')))).resolve()
    if not path.is_dir():
        raise ValueError(f"找不到文件夹 {path}")
    if path == Path(path.anchor) or path == Path.home().resolve():
        raise ValueError(f"{path} 太大了(整个硬盘或用户文件夹),请选里面重要的文件夹,例如文档、照片")
    lowered = str(path).lower()
    if any(part in lowered for part in ("\\windows", "\\program files", "\\appdata", "\\programdata")):
        raise ValueError(f"{path} 是系统或程序的文件夹,不用备份")
    if _inside(path, ASSISTANT_DIR.parent):
        raise ValueError("星期五自己的文件夹不用放进来(它有自己的备份)")
    return path


class FolderBackup:
    def __init__(self, path: str | os.PathLike, clock: Callable[[], float] = time.time) -> None:
        self.path = Path(path)
        self.clock = clock
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        data = data if isinstance(data, dict) else {}
        self.sources: list[str] = [str(s) for s in data.get("sources") or []]
        self.dest: str = str(data.get("dest") or "")
        self.last_ok: float = float(data.get("last_ok") or 0)
        self.last_day: str = str(data.get("last_day") or "")
        self.last_result: str = str(data.get("last_result") or "")
        self.blocked: bool = bool(data.get("blocked"))
        self.reminded: str = str(data.get("reminded") or "")
        self.configured_at: float = float(data.get("configured_at") or 0)
        self.running = False
        self.lock = threading.Lock()

    def _save(self) -> None:
        data = {"sources": self.sources, "dest": self.dest, "last_ok": self.last_ok, "last_day": self.last_day,
                "last_result": self.last_result, "blocked": self.blocked, "reminded": self.reminded,
                "configured_at": self.configured_at}
        temp = self.path.with_name(f"{self.path.name}.{threading.get_ident()}.tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(temp, self.path)

    # ---------- 设定 ----------
    def configure(self, sources: list[str], dest: str) -> str:
        clean = []
        for folder in sources:
            if folder.strip():
                path = check_source(folder)
                if str(path) not in clean:
                    clean.append(str(path))
        if not clean:
            raise ValueError("至少要选一个要备份的文件夹")
        target = Path(os.path.expandvars(os.path.expanduser(dest.strip().strip('"')))) if dest.strip() else None
        if target is None:
            raise ValueError("要填备份到哪里(例如移动硬盘 E:\\星期五备份)")
        target = target.resolve()
        for folder in clean:
            if _inside(target, Path(folder)) or _inside(Path(folder), target):
                raise ValueError("备份的地方不能在要备份的文件夹里面(或反过来)")
        if _inside(target, ASSISTANT_DIR.parent):
            raise ValueError("不能备份到星期五自己的文件夹里")
        import friday_security

        lowered = str(target).lower()
        if (friday_security.is_protected_write(target) or target == Path(target.anchor)
                or any(part in lowered for part in ("\\windows", "\\program files", "\\programdata", "\\appdata"))):
            raise ValueError("不能备份到这里(系统、程序、开机启动的文件夹,或整个硬盘的最上层);在里面建一个文件夹,例如 E:\\星期五备份")
        self.sources, self.dest = clean, str(target)
        self.blocked = False
        self.configured_at = self.configured_at or self.clock()
        self._save()
        same = [f for f in clean if Path(f).anchor.lower() == target.anchor.lower()]
        warn = "(提醒:备份和原文件在同一个硬盘,硬盘坏了会一起没,最好用另一个硬盘或移动硬盘)" if same else ""
        return f"设好了:每天把 {len(clean)} 个文件夹备份到 {target}{warn}。"

    def ready(self) -> bool:
        return bool(self.sources and self.dest)

    # ---------- 备份 ----------
    def _label(self, source: Path, used: set[str]) -> str:
        label = source.name or source.anchor.replace(":", "").strip("\\/") or "备份"
        base, n = label, 2
        while label in used:
            label = f"{base}_{n}"
            n += 1
        used.add(label)
        return label

    def plan(self) -> list[tuple[Path, Path, os.stat_result, bool]]:
        """要复制的:(原文件, 备份位置, 原文件资料, 备份里已经有旧的)。"""
        dest = Path(self.dest)
        used: set[str] = set()
        todo = []
        for source in map(Path, self.sources):
            if not source.is_dir():
                continue
            root = dest / self._label(source, used)
            for folder, dirs, names in os.walk(source):
                dirs[:] = [d for d in dirs if d.lower() not in SKIP_DIRS]
                for name in names:
                    src = Path(folder) / name
                    try:
                        if src.is_symlink():
                            continue
                        stat = src.stat()
                    except OSError:
                        continue
                    target = root / src.relative_to(source)
                    try:
                        old = target.stat()
                    except OSError:
                        todo.append((src, target, stat, False))
                        continue
                    if old.st_size != stat.st_size or abs(old.st_mtime - stat.st_mtime) > 2:
                        todo.append((src, target, stat, True))
        return todo

    def run(self, force: bool = False) -> dict:
        """备份一次。-> {"ok", "copied", "updated", "bytes", "message"}"""
        with self.lock:
            if self.running:
                return {"ok": False, "message": "正在备份中,等一下。"}
            self.running = True
        try:
            return self._run(force)
        finally:
            with self.lock:
                self.running = False

    def _run(self, force: bool) -> dict:
        if not self.ready():
            return {"ok": False, "message": "还没设定要备份哪些文件夹、备份到哪里。"}
        dest = Path(self.dest)
        if not drive_present(dest):
            return {"ok": False, "missing": True, "message": f"备份的硬盘({dest.anchor})没插上,等插上了再备份。"}
        if not dest.exists():
            dest.mkdir(parents=True, exist_ok=True)
        todo = self.plan()
        changed = [t for t in todo if t[3]]
        existing = sum(1 for _ in Path(self.dest).rglob("*")) if changed else 0
        suspicious = [t for t in todo if t[0].suffix.lower() in SUSPICIOUS]
        if not force and (len(suspicious) >= 10 or (len(changed) >= 50 and len(changed) > existing * 0.3)):
            self.blocked = True
            self.last_result = (f"这次没有备份:有 {len(changed)} 个文件同时改了"
                                + (f",其中 {len(suspicious)} 个像被加密的" if suspicious else "")
                                + "。如果是勒索病毒,千万别覆盖备份;如果只是你整理了大批文件,在齿轮设定里点“照样备份”。")
            self._save()
            return {"ok": False, "blocked": True, "message": self.last_result}
        need = sum(t[2].st_size for t in todo)
        try:
            free = shutil.disk_usage(dest).free
        except OSError:
            free = need + 1
        if need > free:
            self.last_result = f"备份的硬盘空间不够:要 {need / 1024 ** 3:.1f} GB,只剩 {free / 1024 ** 3:.1f} GB。"
            self._save()
            return {"ok": False, "message": self.last_result}
        today = datetime.date.fromtimestamp(self.clock()).isoformat()
        copied = updated = failed = 0
        done_bytes = 0
        for src, target, stat, had_old in todo:
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                if had_old:
                    keep = dest / OLD_DIR / today / target.relative_to(dest)
                    keep.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(target, keep)
                shutil.copy2(src, target)
                done_bytes += stat.st_size
                updated += had_old
                copied += not had_old
            except OSError:
                failed += 1
        self._prune(dest)
        self.blocked = False
        self.last_ok = self.clock()
        self.last_day = today
        size = f"{done_bytes / 1024 ** 2:.0f} MB" if done_bytes < 1024 ** 3 else f"{done_bytes / 1024 ** 3:.1f} GB"
        if copied or updated:
            self.last_result = f"备份好了:新文件 {copied} 个、改过的 {updated} 个,共 {size}" + (f",{failed} 个复制失败(可能正在被打开)" if failed else "") + "。"
        else:
            self.last_result = "备份好了:没有新的或改过的文件。"
        self._save()
        return {"ok": True, "copied": copied, "updated": updated, "failed": failed, "bytes": done_bytes,
                "message": self.last_result}

    def _prune(self, dest: Path) -> None:
        old_root = dest / OLD_DIR
        if not old_root.is_dir():
            return
        cutoff = datetime.date.fromtimestamp(self.clock()) - datetime.timedelta(days=KEEP_OLD_DAYS)
        for day in old_root.iterdir():
            try:
                if day.is_dir() and datetime.date.fromisoformat(day.name) < cutoff:
                    shutil.rmtree(day, ignore_errors=True)
            except ValueError:
                continue

    # ---------- 每天 ----------
    def due(self) -> bool:
        now = datetime.datetime.fromtimestamp(self.clock())
        return (self.ready() and not self.blocked and self.last_day != now.date().isoformat()
                and now.hour >= RUN_FROM_HOUR)

    def overdue_message(self, name: str) -> str | None:
        """太久没备份成功(硬盘一直没插):3 天提醒一次。"""
        if not self.ready():
            return None
        now = self.clock()
        since = self.last_ok or self.configured_at or now
        if now - since < REMIND_AFTER_DAYS * 86400:
            return None
        today = datetime.date.fromtimestamp(now)
        if self.reminded and (today - datetime.date.fromisoformat(self.reminded)).days < 3:
            return None
        self.reminded = today.isoformat()
        self._save()
        days = int((now - self.last_ok) / 86400) if self.last_ok else None
        when = f"已经 {days} 天没备份了" if days else "还没备份成功过"
        return f"{name},重要文件夹{when}。备份的硬盘({Path(self.dest).anchor})插上后我会自动备份。"

    def status(self) -> str:
        if not self.ready():
            return "还没设定文件夹备份:在齿轮设定 → 文件夹备份 里选要备份的文件夹和备份到哪里。"
        when = (datetime.datetime.fromtimestamp(self.last_ok).strftime("%m/%d %H:%M") if self.last_ok else "还没有")
        state = "正在备份。" if self.running else ""
        return (f"{state}每天把 {len(self.sources)} 个文件夹备份到 {self.dest}。上次成功:{when}。"
                + (self.last_result or ""))

    def state(self) -> dict:
        return {"sources": self.sources, "dest": self.dest, "running": self.running, "blocked": self.blocked,
                "last": self.status()}
