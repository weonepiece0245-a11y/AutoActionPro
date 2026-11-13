"""半夜自动更新:凌晨 3 点到 5 点、电脑开着但你没在用(20 分钟没碰、没在打游戏)时,星期五自己检查更新;
有新版本就装好、重开。新版本起不来,守护程序会自动退回上一版。早上你回到电脑前时,告诉你更新了什么。

每晚最多检查一次;齿轮设定 → 账号和版本 可以关掉。存在 friday_autoupdate.json。
"""

from __future__ import annotations

import datetime
import json
import os
import threading
from pathlib import Path
from typing import Callable

START_HOUR, END_HOUR = 3, 5
IDLE_SECONDS = 20 * 60
PENDING_HOURS = 3  # 开始自动更新后这么久内重开,才算是这次更新(不然是没装成,别把以后手动更新的当成昨晚的)
MORNING_HOUR = 6
MAX_LINES = 6


def enabled() -> bool:
    return os.environ.get("FRIDAY_AUTO_UPDATE", "on") != "off"


def change_lines(changes: Path) -> list[str]:
    """更新记录里每一条(“- ” 开头的那些)。"""
    try:
        text = Path(changes).read_text(encoding="utf-8")
    except OSError:
        return []
    return [line[2:].strip() for line in text.splitlines() if line.startswith("- ")]


class AutoUpdate:
    def __init__(self, path: str | os.PathLike, changes: str | os.PathLike,
                 clock: Callable[[], datetime.datetime] = datetime.datetime.now) -> None:
        self.path = Path(path)
        self.changes = Path(changes)
        self.clock = clock
        self.lock = threading.Lock()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        self.state: dict = data if isinstance(data, dict) else {}

    def _save(self) -> None:
        temp = self.path.with_name(f"{self.path.name}.{threading.get_ident()}.tmp")
        temp.write_text(json.dumps(self.state, ensure_ascii=False), encoding="utf-8")
        os.replace(temp, self.path)

    def due(self, idle: float | None, busy: bool, gaming: bool) -> bool:
        """现在该不该自己检查更新。"""
        now = self.clock()
        if not enabled() or not START_HOUR <= now.hour < END_HOUR:
            return False
        if self.state.get("checked") == now.strftime("%Y-%m-%d"):
            return False
        return idle is not None and idle >= IDLE_SECONDS and not busy and not gaming

    def mark_started(self) -> None:
        """要开始更新了:记下现在的更新记录,重开后好比较多了什么。"""
        with self.lock:
            self.state.update({"checked": self.clock().strftime("%Y-%m-%d"), "pending": True,
                               "started": self.clock().timestamp(), "before": change_lines(self.changes)})
            self._save()

    def after_start(self) -> None:
        """星期五启动时:如果是半夜自动更新完重开的,准备好早上要说的话。"""
        with self.lock:
            if not self.state.get("pending"):
                return
            before = set(self.state.get("before") or [])
            fresh = self.clock().timestamp() - float(self.state.get("started") or 0) < PENDING_HOURS * 3600
            new = [line for line in change_lines(self.changes) if line not in before] if fresh else []
            self.state["pending"] = False
            self.state.pop("before", None)
            self.state.pop("started", None)
            if new:
                self.state["note"] = new[:MAX_LINES]
                self.state["more"] = max(0, len(new) - MAX_LINES)
            self._save()

    def clear_pending(self) -> None:
        """检查过了,但已经是最新(没有重开):不用等着报告。"""
        with self.lock:
            if self.state.pop("pending", None) is not None:
                self.state.pop("before", None)
                self.state.pop("started", None)
                self._save()

    def morning_note(self, idle: float | None) -> str | None:
        """早上你回到电脑前(刚动过键盘鼠标)时,说一次昨晚更新了什么。"""
        note = self.state.get("note")
        if not note or self.clock().hour < MORNING_HOUR or idle is None or idle > 120:
            return None
        with self.lock:
            more = self.state.pop("more", 0)
            self.state.pop("note", None)
            self._save()
        lines = ";".join(note)
        return f"昨晚我自己更新了:{lines}" + (f";还有 {more} 项" if more else "") + "。想知道细节就问我“你更新了什么”。"
