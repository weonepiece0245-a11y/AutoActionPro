"""功能使用统计:记下每个工具(Claude 用的)和电脑直接办的说法用了几次、出错几次、最后什么时候用,
下次升级就看数据决定先改哪里。说“功能使用统计”或“哪些功能常用”就列出来。

只记次数和时间,不记你说了什么。存在 friday_toolstats.json。
"""

from __future__ import annotations

import datetime
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Callable

SAVE_EVERY = 60  # 秒:最多这么久写一次文件

ASK = re.compile(r"^(星期五)?[,，\s]*(功能|工具)(的)?(使用)?(统计|用量|使用情况)[?？]?$"
                 r"|^(星期五)?[,，\s]*(哪些|什么)(功能|工具)(最)?(常用|没用过|很少用|常出错|最常出错)[?？]?$")


class ToolStats:
    def __init__(self, path: str | os.PathLike, clock: Callable[[], float] = time.time) -> None:
        self.path = Path(path)
        self.clock = clock
        self.lock = threading.Lock()
        self.saved_at = 0.0
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        self.data: dict = data if isinstance(data, dict) else {}
        self.data.setdefault("since", datetime.date.today().isoformat())
        if not isinstance(self.data.get("tools"), dict):
            self.data["tools"] = {}

    def record(self, name: str, ok: bool = True, error: str = "") -> None:
        now = self.clock()
        with self.lock:
            item = self.data["tools"].setdefault(str(name)[:40], {"calls": 0, "errors": 0})
            item["calls"] += 1
            item["last"] = datetime.datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M")
            if not ok:
                item["errors"] += 1
                item["last_error"] = str(error)[:120]
            if now - self.saved_at >= SAVE_EVERY:
                self._save(now)

    def _save(self, now: float) -> None:
        self.saved_at = now
        try:
            temp = self.path.with_name(self.path.name + ".tmp")
            temp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(temp, self.path)
        except OSError:
            pass

    def flush(self) -> None:
        with self.lock:
            self._save(self.clock())

    def describe(self, all_tools: list[str] | None = None) -> str:
        with self.lock:
            tools = {k: dict(v) for k, v in self.data["tools"].items()}
        if not tools:
            return "还没有使用记录(从现在开始记)。"
        ranked = sorted(tools.items(), key=lambda kv: -kv[1]["calls"])
        lines = [f"从 {self.data['since']} 开始记:"]
        lines.append("最常用:" + "、".join(f"{name}({v['calls']} 次)" for name, v in ranked[:8]))
        errors = sorted(((n, v) for n, v in tools.items() if v.get("errors")),
                        key=lambda kv: -kv[1]["errors"] / max(1, kv[1]["calls"]))
        if errors:
            lines.append("常出错:" + ";".join(f"{n} 错 {v['errors']}/{v['calls']} 次(最近:{v.get('last_error', '')})"
                                                for n, v in errors[:5]))
        if all_tools:
            unused = [t for t in all_tools if t not in tools]
            if unused:
                lines.append(f"还没用过({len(unused)} 个):" + "、".join(unused[:30]))
        return "\n".join(lines)

    def state(self) -> dict:
        with self.lock:
            return {"since": self.data["since"], "tools": dict(self.data["tools"])}


class Commands:
    def __init__(self, stats: ToolStats, all_tools: Callable[[], list[str]] = lambda: []) -> None:
        self.stats, self.all_tools = stats, all_tools

    def handle(self, text: str) -> str | None:
        if not ASK.match(re.sub(r"\s+", "", str(text or ""))):
            return None
        return self.stats.describe(self.all_tools())
