"""花费异常提醒和月底预测(看账本,不问 Claude、不花钱):

- 一笔特别大的:比你平常一笔的金额大很多(至少 RM300,而且是平常的 3 倍以上);
- 可能重复:两天内同一个东西、同样金额记了两次(重复扣款或重复记账);
- 某一类突然多:这个月到现在某一类(例如“购物”)比前三个月同一时间平均多一半以上,而且多 RM100 以上;
- 月底预测:照最近 30 天每天花的速度,算这个月大概会花多少;设了每月生活开销预算的话,看会不会超。
同一件事只说一次。说“这个月会花多少”“花费分析”随时看。
"""

from __future__ import annotations

import calendar
import datetime
import os
import re
import statistics
from typing import Callable

BIG_MIN = 300.0
BIG_TIMES = 3.0
SPIKE_RATIO = 1.5
SPIKE_EXTRA = 100.0
DUP_DAYS = 2
ASK = re.compile(r"^(星期五)?[,，\s]*(这个月|本月|月底)?(会|大概|预计)?(花多少|花费预测|开销预测|花费分析|开销分析|月底预测|会不会超支|会超支吗)"
                 r"(钱)?[?？]?$|^(星期五)?[,，\s]*(这个月|本月)(花费|开销)(怎么样|正常吗)[?？]?$")


def budget() -> float:
    try:
        return max(0.0, float(os.environ.get("FRIDAY_SPEND_BUDGET", "0") or 0))
    except ValueError:
        return 0.0


def enabled() -> bool:
    return os.environ.get("FRIDAY_SPEND_ALERTS", "on").strip().lower() not in ("off", "0", "false", "no")


def _day(entry: dict) -> datetime.date | None:
    try:
        return datetime.date.fromisoformat(str(entry.get("date", ""))[:10])
    except ValueError:
        return None


def _amount(entry: dict) -> float:
    try:
        return float(entry.get("amount", 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def _month_start(day: datetime.date, back: int = 0) -> datetime.date:
    year, month = day.year, day.month - back
    while month < 1:
        year, month = year - 1, month + 12
    return datetime.date(year, month, 1)


class SpendWatch:
    def __init__(self, ledger, state, today: Callable[[], datetime.date] = datetime.date.today) -> None:
        self.ledger = ledger
        self.state = state  # friday_alerts.AlertState:说过哪些
        self.today = today

    def entries(self) -> list[tuple[datetime.date, dict]]:
        found = []
        for entry in getattr(self.ledger, "entries", []) or []:
            day = _day(entry)
            if day is not None and _amount(entry) > 0:
                found.append((day, entry))
        return found

    # ---------- 算 ----------
    def forecast(self) -> dict:
        today = self.today()
        start = _month_start(today)
        days_in = calendar.monthrange(today.year, today.month)[1]
        items = self.entries()
        mtd = sum(_amount(e) for d, e in items if start <= d <= today)
        last30 = sum(_amount(e) for d, e in items if today - datetime.timedelta(days=29) <= d <= today)
        recorded_days = {d for d, _ in items}
        history = (today - min(recorded_days)).days + 1 if recorded_days else 0
        daily = last30 / min(30, history) if history else 0.0
        if history < 7:  # 记账不到一周:用这个月自己的速度
            daily = mtd / max(1, today.day)
        left = days_in - today.day
        guess = mtd + daily * left
        return {"month_to_date": round(mtd, 2), "daily": round(daily, 2), "forecast": round(guess, 2),
                "days_left": left, "budget": budget(), "month": today.strftime("%Y-%m")}

    def category_spikes(self) -> list[dict]:
        today = self.today()
        start = _month_start(today)
        items = self.entries()
        now: dict[str, float] = {}
        for d, e in items:
            if start <= d <= today:
                now[e.get("category") or "其他"] = now.get(e.get("category") or "其他", 0) + _amount(e)
        first = min((d for d, _ in items), default=today)
        windows = []  # 前三个月“同一时间”(1 号到同一天);那个月还没开始记账就不算
        for back in (1, 2, 3):
            m0 = _month_start(today, back)
            cut = m0 + datetime.timedelta(days=min(today.day, calendar.monthrange(m0.year, m0.month)[1]) - 1)
            if first <= m0 + datetime.timedelta(days=3):
                windows.append((m0, cut))
        spikes = []
        for cat, spent in now.items():
            past = [sum(_amount(e) for d, e in items if m0 <= d <= cut and (e.get("category") or "其他") == cat)
                    for m0, cut in windows]
            if len(past) < 2 or not any(past):
                continue  # 账记得还不够久:比不出来
            usual = sum(past) / len(past)
            if spent >= usual * SPIKE_RATIO and spent - usual >= SPIKE_EXTRA:
                spikes.append({"category": cat, "now": round(spent, 2), "usual": round(usual, 2)})
        return sorted(spikes, key=lambda s: -(s["now"] - s["usual"]))

    def big_items(self) -> list[dict]:
        today = self.today()
        items = self.entries()
        older = [_amount(e) for d, e in items if today - datetime.timedelta(days=120) <= d < today - datetime.timedelta(days=2)]
        if len(older) < 10:
            return []
        usual = statistics.median(older)
        limit = max(BIG_MIN, usual * BIG_TIMES)
        return [e for d, e in items if d >= today - datetime.timedelta(days=2) and _amount(e) >= limit]

    def duplicates(self) -> list[tuple[dict, dict]]:
        today = self.today()
        recent = sorted([(d, e) for d, e in self.entries() if d >= today - datetime.timedelta(days=DUP_DAYS + 1)],
                        key=lambda x: (x[0], x[1].get("id", 0)))
        pairs = []
        for i, (d1, a) in enumerate(recent):
            for d2, b in recent[i + 1:]:
                if (abs((d2 - d1).days) <= DUP_DAYS and abs(_amount(a) - _amount(b)) < 0.01
                        and re.sub(r"\s+", "", str(a.get("item", "")).lower()) == re.sub(r"\s+", "", str(b.get("item", "")).lower())):
                    pairs.append((a, b))
        return pairs

    # ---------- 要说的 ----------
    def check(self) -> list[str]:
        """新的异常 -> 要提醒的话(说过的不再说)。"""
        if not enabled():
            return []
        said = dict(self.state.data.get("spendwatch") or {})
        out = []
        month = self.today().strftime("%Y-%m")
        for e in self.big_items():
            key = f"big:{e.get('id')}"
            if key not in said:
                said[key] = month
                out.append(f"💸 一笔比较大的花费:{e.get('date')} {e.get('item')} RM{_amount(e):,.2f}(比你平常一笔多很多)。没问题就不用理。")
        for a, b in self.duplicates():
            key = f"dup:{a.get('id')}:{b.get('id')}"
            if key not in said:
                said[key] = month
                out.append(f"💸 可能重复了:{a.get('item')} RM{_amount(a):,.2f} 在 {a.get('date')} 和 {b.get('date')} 各记了一次"
                           f"(#{a.get('id')}、#{b.get('id')})。是重复扣款就查一下银行;是重复记账就说“删掉第 {b.get('id')} 笔账”。")
        for spike in self.category_spikes():
            key = f"spike:{month}:{spike['category']}"
            if key not in said:
                said[key] = month
                out.append(f"💸 这个月「{spike['category']}」到现在花了 RM{spike['now']:,.0f},平常这个时候大约 RM{spike['usual']:,.0f},"
                           f"多了 RM{spike['now'] - spike['usual']:,.0f}。")
        fc = self.forecast()
        if fc["budget"] and self.today().day >= 5:
            if fc["forecast"] > fc["budget"] and f"over:{month}" not in said:
                said[f"over:{month}"] = month
                out.append(f"💸 照最近每天约 RM{fc['daily']:,.0f} 的速度,这个月大概会花 RM{fc['forecast']:,.0f},"
                           f"会超过预算 RM{fc['budget']:,.0f}(还剩 {fc['days_left']} 天,现在 RM{fc['month_to_date']:,.0f})。")
            elif fc["month_to_date"] >= fc["budget"] * 0.8 and f"80:{month}" not in said:
                said[f"80:{month}"] = month
                out.append(f"💸 这个月已经花了 RM{fc['month_to_date']:,.0f},是生活开销预算的 "
                           f"{fc['month_to_date'] / fc['budget']:.0%}(还剩 {fc['days_left']} 天)。")
        if out:
            keep = {k: v for k, v in said.items() if v >= _month_start(self.today(), 3).strftime("%Y-%m")}
            self.state.set("spendwatch", keep)
        return out

    def report(self) -> str:
        fc = self.forecast()
        if not self.entries():
            return "账本里还没有记录,预测不了。记账一阵子之后再问我。"
        lines = [f"这个月到现在花了 RM{fc['month_to_date']:,.2f};照最近每天约 RM{fc['daily']:,.0f} 的速度,"
                 f"月底大概 RM{fc['forecast']:,.0f}(还剩 {fc['days_left']} 天)。"]
        if fc["budget"]:
            gap = fc["budget"] - fc["forecast"]
            lines.append(f"生活开销预算 RM{fc['budget']:,.0f}:" + (f"会超过 RM{-gap:,.0f}。" if gap < 0 else f"大概还能剩 RM{gap:,.0f}。"))
        spikes = self.category_spikes()
        if spikes:
            lines.append("比平常多的:" + ";".join(f"{s['category']} RM{s['now']:,.0f}(平常 RM{s['usual']:,.0f})" for s in spikes[:4]))
        dups = self.duplicates()
        if dups:
            lines.append("可能重复的:" + ";".join(f"{a.get('item')} RM{_amount(a):,.2f}(#{a.get('id')}、#{b.get('id')})" for a, b in dups[:3]))
        return "\n".join(lines)


class Commands:
    def __init__(self, watch: SpendWatch) -> None:
        self.watch = watch

    def handle(self, text: str) -> str | None:
        if ASK.match(re.sub(r"\s+", "", str(text or ""))):
            return self.watch.report()
        return None
