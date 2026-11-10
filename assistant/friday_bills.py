"""账单到期提醒:房租、电费、网费、手机费、订阅这类固定要付的钱,记一次就每期提醒(不花钱)。

每月的账单写几号到期;每年的(路税、保险)再写几月。到期前 3 天、当天各提醒一次,过期 2 天还没付再提醒一次。
说“电费付了”就算这一期付了,顺便记一笔账。存在 friday_bills.json。
"""

from __future__ import annotations

import calendar
import datetime
import json
import os
import threading
import re
from pathlib import Path
from typing import Callable

LEAD_DAYS = (3, 0, -2)  # 前 3 天、当天、过期 2 天
NOTIFY_HOUR = 8
NAME_CHARS = re.compile(r"[\s,，。.、:：]+")


def _due_on(year: int, month: int, day: int) -> datetime.date:
    """这个月的第几天;31 号碰上小月就算月底。"""
    return datetime.date(year, month, min(day, calendar.monthrange(year, month)[1]))


def _shift_month(year: int, month: int, step: int) -> tuple[int, int]:
    index = year * 12 + month - 1 + step
    return index // 12, index % 12 + 1


def money(value) -> str:
    return f"RM {float(value):,.2f}"


class Bills:
    def __init__(self, path: str | os.PathLike, clock: Callable[[], datetime.datetime] = datetime.datetime.now) -> None:
        self.path = Path(path)
        self.clock = clock
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        data = data if isinstance(data, dict) else {}
        self.items: list[dict] = [b for b in (data.get("bills") or []) if isinstance(b, dict) and b.get("name")]
        self.paid: dict[str, str] = dict(data.get("paid") or {})  # "电费|2026-11" -> 付的日期
        self.told: dict[str, str] = dict(data.get("told") or {})

    def _save(self) -> None:
        temp = self.path.with_name(f"{self.path.name}.{threading.get_ident()}.tmp")
        temp.write_text(json.dumps({"bills": self.items, "paid": self.paid, "told": self.told},
                                   ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(temp, self.path)

    # ---------- 记账单 ----------
    def add(self, name: str, day: int, amount=None, month: int | None = None) -> str:
        name = NAME_CHARS.sub(" ", str(name or "")).strip()[:20]
        if not name:
            raise ValueError("要写是什么账单")
        try:
            day = int(day)
            month = int(month) if month else None
            if not 1 <= day <= 31 or (month is not None and not 1 <= month <= 12):
                raise ValueError
            if month is not None:
                datetime.date(2024, month, day)
        except (TypeError, ValueError):
            raise ValueError("日期不对") from None
        try:
            amount = round(float(amount), 2) if amount not in (None, "", 0) else None
        except (TypeError, ValueError):
            raise ValueError("金额不对") from None
        self.items = [b for b in self.items if b["name"] != name]
        self.items.append({"name": name, "day": day, "month": month, "amount": amount})
        self._save()
        when = f"每年 {month} 月 {day} 日" if month else f"每月 {day} 号"
        cost = f",一般 {money(amount)}" if amount else ""
        return f"记好了:{name}{when}到期{cost}。到期前 3 天和当天会提醒你,付了跟我说一声“{name}付了”。"

    def find(self, name: str) -> dict | None:
        name = NAME_CHARS.sub("", str(name or "")).lower()
        if not name:
            return None
        for bill in self.items:
            if bill["name"].replace(" ", "").lower() == name:
                return bill
        hits = [b for b in self.items if name in b["name"].replace(" ", "").lower()
                or b["name"].replace(" ", "").lower() in name]
        return hits[0] if len(hits) == 1 else None

    def remove(self, name: str) -> str:
        bill = self.find(name)
        if bill is None:
            raise ValueError(f"没有“{name}”这个账单")
        self.items.remove(bill)
        self.paid = {k: v for k, v in self.paid.items() if not k.startswith(bill["name"] + "|")}
        self._save()
        return f"删掉了“{bill['name']}”,以后不再提醒"

    # ---------- 每一期 ----------
    @staticmethod
    def _cycles(bill: dict, today: datetime.date) -> list[tuple[str, datetime.date]]:
        """上一期、这一期、下一期:(期号, 到期日)。"""
        if bill.get("month"):
            return [(str(y), _due_on(y, bill["month"], bill["day"])) for y in (today.year - 1, today.year, today.year + 1)]
        found = []
        for step in (-1, 0, 1):
            y, m = _shift_month(today.year, today.month, step)
            found.append((f"{y}-{m:02d}", _due_on(y, m, bill["day"])))
        return found

    def _is_paid(self, bill: dict, cycle: str) -> bool:
        return f"{bill['name']}|{cycle}" in self.paid

    def _open_cycle(self, bill: dict, today: datetime.date) -> tuple[str, datetime.date] | None:
        """现在该付的那一期:过期 20 天内没付的上一期,不然就是最近要到期的没付的那一期(30 天内)。"""
        for cycle, due in self._cycles(bill, today):
            if self._is_paid(bill, cycle):
                continue
            if -20 <= (due - today).days <= 31:
                return cycle, due
        return None

    def mark_paid(self, name: str, amount=None) -> tuple[str, dict, float | None]:
        """-> (回答, 账单, 要记账的金额)。"""
        bill = self.find(name)
        if bill is None:
            raise ValueError(f"没有“{name}”这个账单")
        today = self.clock().date()
        found = self._open_cycle(bill, today)
        if found is None:
            return f"{bill['name']}最近这一期已经记过付了。", bill, None
        cycle, due = found
        self.paid[f"{bill['name']}|{cycle}"] = today.isoformat()
        cutoff = (today - datetime.timedelta(days=800)).isoformat()
        self.paid = {k: v for k, v in self.paid.items() if v >= cutoff}
        self._save()
        try:
            amount = float(amount) if amount not in (None, "") else bill.get("amount")
        except (TypeError, ValueError):
            amount = bill.get("amount")
        text = f"好,{bill['name']}({due.month}/{due.day} 到期的那一期)记为已付"
        return text + (f",也记了一笔账 {money(amount)}。" if amount else "。"), bill, amount

    # ---------- 查 ----------
    def upcoming(self, days: int = 31) -> list[tuple[datetime.date, dict, bool]]:
        """接下来(含过期没付的)要付的:(到期日, 账单, 付了没)。"""
        today = self.clock().date()
        found = []
        for bill in self.items:
            for cycle, due in self._cycles(bill, today):
                left = (due - today).days
                paid = self._is_paid(bill, cycle)
                if 0 <= left <= days or (left < 0 and left >= -20 and not paid):
                    found.append((due, bill, paid))
        return sorted(found, key=lambda x: x[0])

    def describe(self, days: int = 31) -> str:
        if not self.items:
            return "还没有记账单。跟我说“每个月 15 号交房租 800”这样就能记。"
        today = self.clock().date()
        parts = []
        for due, bill, paid in self.upcoming(days):
            left = (due - today).days
            when = "今天到期" if left == 0 else (f"过期 {-left} 天了" if left < 0 else f"{left} 天后")
            cost = f" {money(bill['amount'])}" if bill.get("amount") else ""
            parts.append(f"{bill['name']}{cost}({due.month}/{due.day},{'已付' if paid else when})")
        if not parts:
            return f"接下来 {days} 天没有要付的账单。"
        unpaid = sum(1 for _, _, paid in self.upcoming(days) if not paid)
        head = f"接下来要付的账单(还有 {unpaid} 个没付):" if unpaid else "接下来的账单都付了:"
        return head + "、".join(parts) + "。"

    def describe_all(self) -> str:
        if not self.items:
            return "还没有记账单。"
        parts = []
        for bill in sorted(self.items, key=lambda b: (b.get("month") or 0, b["day"])):
            when = f"每年 {bill['month']} 月 {bill['day']} 日" if bill.get("month") else f"每月 {bill['day']} 号"
            parts.append(f"{bill['name']}({when}" + (f",{money(bill['amount'])}" if bill.get("amount") else "") + ")")
        return "记着的账单:" + "、".join(parts) + "。"

    def due(self, name: str) -> list[str]:
        """该提醒的(前 3 天、当天、过期 2 天还没付;早上 8 点后)。"""
        now = self.clock()
        if now.hour < NOTIFY_HOUR:
            return []
        today = now.date()
        said = []
        for bill in self.items:
            for cycle, due in self._cycles(bill, today):
                left = (due - today).days
                if left not in LEAD_DAYS or self._is_paid(bill, cycle):
                    continue
                key = f"{bill['name']}|{cycle}|{left}"
                if key in self.told:
                    continue
                self.told[key] = today.isoformat()
                cost = f"({money(bill['amount'])})" if bill.get("amount") else ""
                if left > 0:
                    said.append(f"{name},{bill['name']}{cost} {left} 天后({due.month}/{due.day})到期,记得付。")
                elif left == 0:
                    said.append(f"{name},{bill['name']}{cost}今天到期。付了跟我说一声“{bill['name']}付了”。")
                else:
                    said.append(f"{name},{bill['name']}{cost}已经过期 {-left} 天了,还没记到付款,要不要现在付?")
        if said:
            cutoff = (today - datetime.timedelta(days=400)).isoformat()
            self.told = {k: v for k, v in self.told.items() if v >= cutoff}
            self._save()
        return said

    def brief(self, days: int = 3) -> str:
        """早安简报用:3 天内到期或过期没付的。"""
        today = self.clock().date()
        parts = []
        for due, bill, paid in self.upcoming(days):
            if paid:
                continue
            left = (due - today).days
            parts.append(f"{bill['name']}" + ("今天到期" if left == 0 else f"过期 {-left} 天" if left < 0 else f"{left} 天后到期"))
        return "、".join(parts)
