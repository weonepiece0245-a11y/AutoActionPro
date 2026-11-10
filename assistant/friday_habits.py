"""习惯打卡:喝水、运动、吃药、早睡这类每天要做的事。到点提醒,说“水喝了”“药吃了”就打卡(不花钱)。

每个习惯有每天的目标(喝水 8 杯、吃药 2 次),可以设几个提醒时间;记着连续达标几天,每周报告完成度。
存在 friday_habits.json。
"""

from __future__ import annotations

import datetime
import json
import math
import os
import re
from pathlib import Path
from typing import Callable

REMIND_WINDOW_MINUTES = 120  # 过了提醒时间 2 小时还没开星期五,就不补提醒了
VERBS = "喝吃做去练跑读背打写睡"


def _keys(name: str) -> list[str]:
    """“喝水”也认“水”,“吃药”也认“药”。"""
    keys = [name]
    if len(name) >= 2 and name[0] in VERBS:
        keys.append(name[1:])
    return keys


class Habits:
    def __init__(self, path: str | os.PathLike, clock: Callable[[], datetime.datetime] = datetime.datetime.now) -> None:
        self.path = Path(path)
        self.clock = clock
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        data = data if isinstance(data, dict) else {}
        self.items: list[dict] = [h for h in (data.get("habits") or []) if isinstance(h, dict) and h.get("name")]
        self.log: dict[str, dict[str, int]] = {k: dict(v) for k, v in (data.get("log") or {}).items() if isinstance(v, dict)}
        self.told: dict[str, str] = dict(data.get("told") or {})

    def _save(self) -> None:
        temp = self.path.with_name(self.path.name + ".tmp")
        temp.write_text(json.dumps({"habits": self.items, "log": self.log, "told": self.told}, ensure_ascii=False, indent=1),
                        encoding="utf-8")
        os.replace(temp, self.path)

    def _today(self) -> str:
        return self.clock().strftime("%Y-%m-%d")

    # ---------- 设定 ----------
    def add(self, name: str, target: int = 1, times: list[str] | None = None, unit: str = "次") -> str:
        name = re.sub(r"\s+", "", str(name or ""))[:12]
        if not name:
            raise ValueError("要写是什么习惯")
        try:
            target = int(target or 1)
        except (TypeError, ValueError):
            raise ValueError("每天的目标要是数字") from None
        if not 1 <= target <= 50:
            raise ValueError("每天的目标在 1 到 50 之间")
        clean_times = []
        for t in times or []:
            found = re.fullmatch(r"\s*(\d{1,2})[::.](\d{2})\s*", str(t))
            if not found or int(found.group(1)) > 23 or int(found.group(2)) > 59:
                raise ValueError(f"提醒时间“{t}”不对,要像 09:00 这样")
            clean_times.append(f"{int(found.group(1)):02d}:{found.group(2)}")
        unit = (str(unit or "次").strip() or "次")[:3]
        self.items = [h for h in self.items if h["name"] != name]
        self.items.append({"name": name, "target": target, "times": sorted(set(clean_times)), "unit": unit})
        self._save()
        when = ("每天 " + "、".join(sorted(set(clean_times))) + " 提醒") if clean_times else "不定时提醒"
        return f"记好了:{name},每天 {target} {unit},{when}。做完跟我说一声“{name}好了”就打卡。"

    def find(self, name: str) -> dict | None:
        name = re.sub(r"\s+", "", str(name or ""))
        for habit in self.items:
            if habit["name"] == name:
                return habit
        hits = [h for h in self.items if any(k in name for k in _keys(h["name"])) or (name and name in h["name"])]
        return hits[0] if len(hits) == 1 else None

    def remove(self, name: str) -> str:
        habit = self.find(name)
        if habit is None:
            raise ValueError(f"没有“{name}”这个习惯")
        self.items.remove(habit)
        self._save()
        return f"删掉了“{habit['name']}”,以后不再提醒"

    # ---------- 打卡 ----------
    def done_today(self, habit: dict) -> int:
        return self.log.get(self._today(), {}).get(habit["name"], 0)

    def streak(self, habit: dict) -> int:
        day = self.clock().date()
        if self.log.get(day.isoformat(), {}).get(habit["name"], 0) < habit["target"]:
            day -= datetime.timedelta(days=1)  # 今天还没做完:从昨天算起
        count = 0
        while self.log.get(day.isoformat(), {}).get(habit["name"], 0) >= habit["target"]:
            count += 1
            day -= datetime.timedelta(days=1)
        return count

    def check_in(self, name: str, count: int = 1) -> str:
        habit = self.find(name)
        if habit is None:
            raise ValueError(f"没有“{name}”这个习惯")
        count = max(1, min(int(count or 1), 50))
        today = self._today()
        before = self.log.setdefault(today, {}).get(habit["name"], 0)
        now = before + count
        self.log[today][habit["name"]] = now
        cutoff = (self.clock().date() - datetime.timedelta(days=400)).isoformat()
        self.log = {k: v for k, v in self.log.items() if k >= cutoff}
        self._save()
        target, unit = habit["target"], habit["unit"]
        if before < target <= now:
            days = self.streak(habit)
            return f"👍 今天的{habit['name']}完成了({now}/{target} {unit})" + (f",已经连续 {days} 天!" if days > 1 else "!")
        if now > target:
            return f"{habit['name']}打卡:今天 {now} {unit}(目标 {target},已经超额了)。"
        return f"{habit['name']}打卡:今天 {now}/{target} {unit},还差 {target - now} {unit}。"

    def undo(self, name: str) -> str:
        habit = self.find(name)
        if habit is None or not self.done_today(habit):
            raise ValueError("今天没有可以撤销的打卡")
        self.log[self._today()][habit["name"]] -= 1
        self._save()
        return f"撤销了一次{habit['name']}打卡,今天 {self.done_today(habit)}/{habit['target']} {habit['unit']}。"

    # ---------- 提醒 ----------
    def due(self, name: str) -> list[str]:
        now = self.clock()
        said = []
        for habit in self.items:
            times = habit.get("times") or []
            for k, t in enumerate(times):
                hour, minute = map(int, t.split(":"))
                at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                if not at <= now < at + datetime.timedelta(minutes=REMIND_WINDOW_MINUTES):
                    continue
                expected = math.ceil(habit["target"] * (k + 1) / len(times))
                done = self.done_today(habit)
                key = f"{habit['name']}|{self._today()}|{t}"
                if done >= expected or key in self.told:
                    continue
                self.told[key] = self._today()
                progress = f"(今天 {done}/{habit['target']} {habit['unit']})" if habit["target"] > 1 else ""
                said.append(f"{name},该{habit['name']}了{progress}。做完跟我说一声就打卡。")
        if said:
            cutoff = (now.date() - datetime.timedelta(days=3)).isoformat()
            self.told = {k: v for k, v in self.told.items() if v >= cutoff}
            self._save()
        return said

    # ---------- 报告 ----------
    def report(self, period: str = "today") -> str:
        if not self.items:
            return "还没有习惯。跟我说“每天喝 8 杯水,10 点、1 点、4 点提醒我”这样就能加。"
        if period == "week":
            return self.week_line() or "这周还没有打卡。"
        parts = []
        for habit in self.items:
            done = self.done_today(habit)
            mark = "✓" if done >= habit["target"] else ""
            days = self.streak(habit)
            parts.append(f"{habit['name']} {done}/{habit['target']} {habit['unit']}{mark}" + (f"(连续 {days} 天)" if days > 1 else ""))
        return "今天的习惯:" + "、".join(parts) + "。"

    def week_line(self, last: datetime.date | None = None) -> str:
        """这一周(星期一到 last)每个习惯达标几天。"""
        last = last or self.clock().date()
        first = last - datetime.timedelta(days=last.weekday())
        days = [(first + datetime.timedelta(days=i)).isoformat() for i in range((last - first).days + 1)]
        parts = []
        for habit in self.items:
            met = sum(1 for d in days if self.log.get(d, {}).get(habit["name"], 0) >= habit["target"])
            parts.append(f"{habit['name']} {met}/{len(days)} 天达标")
        return ("习惯打卡:" + "、".join(parts) + "。") if parts else ""

    def match_sentence(self, sentence: str) -> tuple[dict, int] | None:
        """“水喝了”“吃了药”“喝了两杯水”“跑步打卡” -> (习惯, 次数);不像打卡就返回 None。"""
        if len(sentence) > 14 or re.search(r"没|吗|不|提醒|几|多少|什么|删|改|撤销|设", sentence):
            return None
        if not ("了" in sentence or "打卡" in sentence or "完成" in sentence):
            return None
        hits = [h for h in self.items if any(k in sentence for k in _keys(h["name"]))]
        if len(hits) != 1:
            return None
        count = 1
        found = re.search(r"(\d{1,2}|[一二两三四五六七八九十]{1,2})(杯|次|个|颗|粒|组|片)", sentence)
        if found:
            import friday_focus

            count = friday_focus.cn_number(found.group(1)) or 1
        return hits[0], count
