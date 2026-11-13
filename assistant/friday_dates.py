"""生日和纪念日提醒:Google 联系人里的生日,加上你告诉星期五的重要日子,提前 3 天和当天提醒(不花钱)。

自己加的日子存在 friday_dates.json;Google 联系人的生日每天读一次。
"""

from __future__ import annotations

import datetime
import json
import os
from pathlib import Path
from typing import Callable

LEAD_DAYS = (3, 0)
NOTIFY_HOUR = 8  # 早上 8 点后才提醒
KINDS = ["生日", "纪念日", "其他"]


def next_occurrence(month: int, day: int, today: datetime.date) -> datetime.date:
    for year in (today.year, today.year + 1):
        try:
            when = datetime.date(year, month, day)
        except ValueError:  # 2 月 29 日:不是闰年就算 2 月 28 日
            when = datetime.date(year, month, 28)
        if when >= today:
            return when
    return datetime.date(today.year + 1, month, min(day, 28))


class Dates:
    def __init__(self, path: str | os.PathLike, contacts: Callable[[], list[dict]] | None = None,
                 clock: Callable[[], datetime.datetime] = datetime.datetime.now) -> None:
        self.path = Path(path)
        self.contacts = contacts  # () -> [{"name", "month", "day", "year"}](Google 联系人的生日)
        self.clock = clock
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        data = data if isinstance(data, dict) else {}
        self.items: list[dict] = [d for d in (data.get("dates") or []) if isinstance(d, dict)]
        self.told: dict[str, str] = dict(data.get("told") or {})
        self._contact_cache: tuple[str, list[dict]] = ("", [])

    def _save(self) -> None:
        temp = self.path.with_name(self.path.name + ".tmp")
        temp.write_text(json.dumps({"dates": self.items, "told": self.told}, ensure_ascii=False, indent=1),
                        encoding="utf-8")
        os.replace(temp, self.path)

    # ---------- 自己加的日子 ----------
    def add(self, name: str, month: int, day: int, kind: str = "生日", year: int | None = None) -> str:
        name = " ".join(str(name or "").split())[:30]
        if not name:
            raise ValueError("要写是谁的、什么日子")
        try:
            month, day = int(month), int(day)
            datetime.date(2024, month, day)  # 2024 是闰年:2 月 29 日也算对
        except (TypeError, ValueError):
            raise ValueError("日期不对") from None
        kind = kind if kind in KINDS else "其他"
        year = int(year) if year else None
        self.items = [d for d in self.items if not (d["name"] == name and d["kind"] == kind)]
        self.items.append({"name": name, "month": month, "day": day, "kind": kind, "year": year})
        self._save()
        return f"记好了:{name}的{kind}是 {month} 月 {day} 日,提前 3 天和当天会提醒你。"

    def remove(self, name: str) -> str:
        before = len(self.items)
        self.items = [d for d in self.items if name not in d["name"]]
        if len(self.items) == before:
            raise ValueError(f"没有“{name}”的日子")
        self._save()
        return f"删掉了“{name}”的日子"

    # ---------- 查 ----------
    def _all(self) -> list[dict]:
        found = list(self.items)
        if self.contacts is not None:
            today = self.clock().strftime("%Y-%m-%d")
            if self._contact_cache[0] != today:
                try:
                    self._contact_cache = (today, self.contacts())
                except Exception:
                    self._contact_cache = (today, self._contact_cache[1])
            mine = {(d["name"], d["kind"]) for d in found}
            found += [{**c, "kind": "生日", "source": "Google 联系人"} for c in self._contact_cache[1]
                      if (c["name"], "生日") not in mine]
        return found

    def upcoming(self, days: int = 30) -> list[tuple[datetime.date, dict]]:
        today = self.clock().date()
        result = []
        for item in self._all():
            when = next_occurrence(item["month"], item["day"], today)
            if (when - today).days <= days:
                result.append((when, item))
        return sorted(result, key=lambda pair: pair[0])

    @staticmethod
    def _label(item: dict, when: datetime.date) -> str:
        text = f"{item['name']}的{item['kind']}"
        if item.get("year") and item["kind"] in ("生日", "纪念日"):
            count = when.year - int(item["year"])
            if count > 0:
                text += f"({count} {'岁' if item['kind'] == '生日' else '周年'})"
        return text

    def describe_upcoming(self, days: int = 30) -> str:
        today = self.clock().date()
        found = self.upcoming(days)
        if not found:
            return f"接下来 {days} 天没有生日或纪念日。"
        parts = []
        for when, item in found:
            left = (when - today).days
            parts.append(f"{when.month}/{when.day} {self._label(item, when)}" + ("(今天)" if left == 0 else f"({left} 天后)"))
        return "接下来的重要日子:" + "、".join(parts) + "。"

    def due(self, name: str) -> list[str]:
        """该提醒的(提前 3 天、当天各一次,早上 8 点后)。"""
        now = self.clock()
        if now.hour < NOTIFY_HOUR:
            return []
        said = []
        for when, item in self.upcoming(max(LEAD_DAYS)):
            left = (when - now.date()).days
            if left not in LEAD_DAYS:
                continue
            key = f"{item['name']}|{item['kind']}|{when.isoformat()}|{left}"
            if key in self.told:
                continue
            self.told[key] = now.strftime("%Y-%m-%d")
            label = self._label(item, when)
            if left == 0:
                said.append(f"{name},今天是{label}。要我帮你想几句祝福的话吗?")
            else:
                said.append(f"{name},{left} 天后({when.month}/{when.day})是{label}。要准备礼物或者想祝福的话,跟我说一声。")
        if said:
            cutoff = (now - datetime.timedelta(days=400)).strftime("%Y-%m-%d")
            self.told = {k: v for k, v in self.told.items() if v >= cutoff}
            self._save()
        return said


def google_birthdays(google) -> Callable[[], list[dict]]:
    """读 Google 联系人里填了生日的人。"""

    def load() -> list[dict]:
        people = google.account.service("people").people()
        found, token = [], None
        for _ in range(10):
            result = people.connections().list(resourceName="people/me", personFields="names,birthdays",
                                               pageSize=1000, pageToken=token).execute()
            for person in result.get("connections", []):
                name = (person.get("names") or [{}])[0].get("displayName")
                for birthday in person.get("birthdays", []):
                    date = birthday.get("date") or {}
                    if name and date.get("month") and date.get("day"):
                        found.append({"name": name, "month": date["month"], "day": date["day"], "year": date.get("year")})
                        break
            token = result.get("nextPageToken")
            if not token:
                break
        return found

    return load
