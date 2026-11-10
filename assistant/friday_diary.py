"""每日总结和日记:晚上说“今天总结一下”(或到点自动),说今天用了多久电脑、玩了什么、专注、习惯、花了多少、
记了什么笔记;再问你“今天过得怎么样”,你说“日记:……”就记下来。以后可以问“上周三的日记”“日记里找 旅行”。

全在电脑上做,不花钱。日记存在 friday_diary.json。
"""

from __future__ import annotations

import datetime
import json
import os
import re
import threading
from pathlib import Path
from typing import Callable

WEEKDAYS = "一二三四五六日"
DEFAULT_TIME = "22:30"
CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "日": 7, "天": 7, "七": 7}


def diary_time() -> str:
    value = os.environ.get("FRIDAY_DIARY_TIME", DEFAULT_TIME).strip().lower()
    return value if value == "off" or re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", value) else DEFAULT_TIME


def parse_day(text: str, today: datetime.date) -> datetime.date | None:
    """今天 / 昨天 / 前天 / 上周三 / 这周一 / 星期二 / 11月5号 / 2026-11-05 -> 日期。"""
    text = text.strip()
    simple = {"今天": 0, "今日": 0, "昨天": 1, "昨日": 1, "前天": 2, "大前天": 3}
    if text in simple:
        return today - datetime.timedelta(days=simple[text])
    found = re.fullmatch(r"(上上|上|这|本)?(个)?(周|星期|礼拜)([一二三四五六日天七])", text)
    if found:
        weekday = CN_NUM[found.group(4)] - 1
        monday = today - datetime.timedelta(days=today.weekday())
        offset = {"上上": -14, "上": -7}.get(found.group(1) or "", 0)
        day = monday + datetime.timedelta(days=offset + weekday)
        if not found.group(1) and day > today:
            day -= datetime.timedelta(days=7)  # 只说“星期二”:最近过去的那个
        return day
    found = re.fullmatch(r"(?:(\d{4})[年-])?(\d{1,2})[月-](\d{1,2})[日号]?", text)
    if found:
        year = int(found.group(1) or today.year)
        try:
            day = datetime.date(year, int(found.group(2)), int(found.group(3)))
        except ValueError:
            return None
        if not found.group(1) and day > today:
            day = day.replace(year=year - 1)
        return day
    return None


def label(day: datetime.date) -> str:
    return f"{day.month} 月 {day.day} 日(星期{WEEKDAYS[day.weekday()]})"


class Diary:
    def __init__(self, path: str | os.PathLike, clock: Callable[[], datetime.datetime] = datetime.datetime.now) -> None:
        self.path = Path(path)
        self.clock = clock
        self.lock = threading.Lock()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        self.days: dict[str, dict] = {k: v for k, v in (data.get("days") or {}).items() if isinstance(v, dict)} \
            if isinstance(data, dict) else {}
        self.asked: str = str(data.get("asked") or "") if isinstance(data, dict) else ""
        self.sources: list[Callable[[datetime.date], str | None]] = []  # 今天的各项统计(主程序接上)

    def _save(self) -> None:
        temp = self.path.with_name(f"{self.path.name}.{threading.get_ident()}.tmp")
        temp.write_text(json.dumps({"days": self.days, "asked": self.asked}, ensure_ascii=False, indent=1),
                        encoding="utf-8")
        os.replace(temp, self.path)

    # ---------- 总结 ----------
    def summary(self, day: datetime.date | None = None) -> str:
        day = day or self.clock().date()
        lines = []
        for source in self.sources:
            try:
                line = source(day)
            except Exception:
                continue
            if line:
                lines.append(line)
        head = "今天" if day == self.clock().date() else label(day)
        if not lines:
            return f"{head}没有什么记录。"
        text = f"{head}的总结:\n" + "\n".join(f"· {line}" for line in lines)
        with self.lock:
            entry = self.days.setdefault(day.isoformat(), {})
            entry["summary"] = text
            self._save()
        return text

    def evening_due(self, idle: float | None) -> bool:
        """到点了、人在电脑前、今天还没问过:晚上主动做一次总结。"""
        when = diary_time()
        now = self.clock()
        if when == "off" or self.asked == self._day().isoformat() or idle is None or idle > 120:
            return False
        hour, minute = map(int, when.split(":"))
        return (now.hour, now.minute) >= (hour, minute) or now.hour < 3  # 过了半夜还没睡:也算这一天

    def _day(self) -> datetime.date:
        """“这一天”:凌晨 3 点前还算前一天(熬夜写的日记算昨天的)。"""
        now = self.clock()
        return now.date() if now.hour >= 3 else now.date() - datetime.timedelta(days=1)

    def evening(self, name: str) -> str:
        day = self._day()
        with self.lock:
            self.asked = day.isoformat()
            self._save()
        return self.summary(day) + f"\n{name},今天过得怎么样?想记下来的话,说“日记:……”。"

    # ---------- 日记 ----------
    def write(self, text: str, day: datetime.date | None = None) -> str:
        text = " ".join(str(text or "").split())[:3000]
        if not text:
            raise ValueError("日记要写点什么")
        now = self.clock()
        day = day or self._day()  # 半夜写的算前一天
        with self.lock:
            entry = self.days.setdefault(day.isoformat(), {})
            entry["entry"] = (entry.get("entry", "") + "\n" + text).strip() if entry.get("entry") else text
            entry["written"] = now.strftime("%H:%M")
            self._save()
        return f"记好了,这是 {label(day)} 的日记。"

    def read(self, day: datetime.date) -> str:
        entry = self.days.get(day.isoformat()) or {}
        parts = []
        if entry.get("entry"):
            parts.append(f"日记:{entry['entry']}")
        if entry.get("summary"):
            parts.append(entry["summary"].split("\n", 1)[-1])
        if not parts:
            return f"{label(day)}没有日记。"
        return f"{label(day)}\n" + "\n".join(parts)

    def search(self, query: str, limit: int = 8) -> str:
        query = query.strip().lower()
        if not query:
            raise ValueError("要找什么?")
        hits = [(d, e) for d, e in sorted(self.days.items(), reverse=True)
                if query in (e.get("entry", "") + " " + e.get("summary", "")).lower()]
        if not hits:
            return f"日记里没有“{query}”。"
        lines = []
        for d, e in hits[:limit]:
            text = e.get("entry") or e.get("summary", "")
            at = text.lower().find(query)
            snippet = text[max(0, at - 20): at + 40].replace("\n", " ")
            lines.append(f"{label(datetime.date.fromisoformat(d))}:…{snippet}…")
        return f"找到 {len(hits)} 天:\n" + "\n".join(lines)
