"""家里用电统计:会计电的 Tapo 插座(例如 P110、P115)每天用了几度电、大概多少钱(按 TNB 电费,可以在设定改),
每个星期天晚上告诉你哪个最耗电、和上星期比;半夜一直在待机耗电的会提醒你。

每 15 分钟读一次插座(在家里网络直接读,不花钱)。电费只是估算:默认每度 RM0.45(大约是 TNB 住宅用电含各种收费的平均),
你的电单如果不一样,在 齿轮设定 → 生活 → 家里用电 改。存在 friday_energy.json(只留 90 天)。
"""

from __future__ import annotations

import datetime
import json
import os
import re
import threading
from pathlib import Path
from typing import Callable

KEEP_DAYS = 90
DEFAULT_RATE = 0.45
STANDBY_MIN_W, STANDBY_MAX_W = 1.0, 30.0
NIGHT_HOURS = range(1, 6)
STANDBY_REMIND_DAYS = 7

ASK = re.compile(r"^(星期五)?[,，\s]*(家里)?(今天|这周|这个星期|本周|这个月|本月|最近)?(的)?(用电|用了多少电|用了几度电|电费|耗电)"
                 r"(多少|情况|怎么样|报告|统计)?[?？]?$|^(星期五)?[,，\s]*(哪个|什么)(电器|插座|东西)?(最耗电|最费电|用电最多)[?？]?$")


def rate() -> float:
    try:
        value = float(os.environ.get("FRIDAY_ELECTRIC_RATE", DEFAULT_RATE))
    except ValueError:
        return DEFAULT_RATE
    return value if 0.05 <= value <= 3 else DEFAULT_RATE


def money(kwh: float) -> str:
    return f"RM{kwh * rate():,.2f}"


class EnergyMeter:
    def __init__(self, path: str | os.PathLike, clock: Callable[[], datetime.datetime] = datetime.datetime.now) -> None:
        self.path = Path(path)
        self.clock = clock
        self.lock = threading.Lock()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        self.data: dict = data if isinstance(data, dict) else {}
        for key in ("days", "now", "night", "standby_told"):
            if not isinstance(self.data.get(key), dict):
                self.data[key] = {}

    def _save(self) -> None:
        temp = self.path.with_name(self.path.name + ".tmp")
        temp.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")
        os.replace(temp, self.path)

    # ---------- 读插座 ----------
    def record(self, readings: dict[str, dict]) -> list[str]:
        """readings:{设备名: {power, today, month, on}}。返回要提醒的话(半夜待机耗电)。"""
        now = self.clock()
        today = now.date().isoformat()
        notes = []
        with self.lock:
            day = self.data["days"].setdefault(today, {})
            for name, r in readings.items():
                if r is None:
                    continue
                day[name] = round(max(day.get(name, 0), float(r.get("today") or 0)), 3)
                self.data["now"][name] = {"power": round(float(r.get("power") or 0), 1), "month": round(float(r.get("month") or 0), 3),
                                          "on": bool(r.get("on")), "time": now.strftime("%H:%M")}
                night = self.data["night"].setdefault(name, {"date": today, "samples": []})
                if night.get("date") != today:
                    night.update(date=today, samples=[])
                if now.hour in NIGHT_HOURS:
                    night["samples"].append(float(r.get("power") or 0))
            if now.hour == 6:
                notes = self._standby(today)
            for old in sorted(self.data["days"])[:-KEEP_DAYS]:
                self.data["days"].pop(old, None)
            self._save()
        return notes

    def _standby(self, today: str) -> list[str]:
        notes = []
        for name, night in self.data["night"].items():
            samples = night.get("samples") or []
            if night.get("date") != today or len(samples) < 6:
                continue
            if all(STANDBY_MIN_W <= s <= STANDBY_MAX_W for s in samples):
                told = self.data["standby_told"].get(name, "")
                if told and (datetime.date.fromisoformat(today) - datetime.date.fromisoformat(told)).days < STANDBY_REMIND_DAYS:
                    continue
                watts = sum(samples) / len(samples)
                month_kwh = watts * 24 * 30 / 1000
                notes.append(f"{name} 半夜一直在待机用电(大约 {watts:.0f} 瓦),一整个月就是 {month_kwh:.1f} 度、约 {money(month_kwh)}。"
                             f"不用的时候可以把插座关掉,或者设一个“睡觉关掉 {name}”的场景。")
                self.data["standby_told"][name] = today
        return notes

    # ---------- 看 ----------
    def totals(self, first: datetime.date, last: datetime.date) -> dict[str, float]:
        out: dict[str, float] = {}
        for day, devices in self.data["days"].items():
            try:
                d = datetime.date.fromisoformat(day)
            except ValueError:
                continue
            if first <= d <= last:
                for name, kwh in devices.items():
                    out[name] = out.get(name, 0) + kwh
        return out

    def describe(self, period: str = "") -> str:
        today = self.clock().date()
        if not self.data["now"]:
            return "还没有用电资料:要有会计电的 Tapo 插座(例如 P110、P115),而且已经连上 Tapo。"
        if period in ("今天",):
            first, label = today, "今天"
        elif period in ("这周", "这个星期", "本周", "最近"):
            first, label = today - datetime.timedelta(days=today.weekday()), "这星期"
        else:
            first, label = today.replace(day=1), "这个月"
        totals = self.totals(first, today)
        if label == "这个月":  # 插座自己记的这个月更准(不怕星期五关着时漏掉)
            for name, info in self.data["now"].items():
                totals[name] = max(totals.get(name, 0), info.get("month", 0))
        if not any(totals.values()):
            return f"{label}还没有用电记录。"
        ranked = sorted(totals.items(), key=lambda kv: -kv[1])
        total = sum(totals.values())
        lines = [f"- {name}:{kwh:.1f} 度,约 {money(kwh)}" for name, kwh in ranked if kwh > 0]
        now_on = [f"{n} {i['power']:.0f} 瓦" for n, i in self.data["now"].items() if i.get("power", 0) >= 1]
        text = f"{label}插座一共用了 {total:.1f} 度电,约 {money(total)}(每度 RM{rate():.2f} 估算):\n" + "\n".join(lines)
        text += f"\n最耗电的是 {ranked[0][0]}。"
        if now_on:
            text += "现在在用电的:" + "、".join(now_on) + "。"
        return text

    def weekly(self) -> str | None:
        """星期天晚上:这星期 vs 上星期(每星期说一次)。"""
        now = self.clock()
        if now.weekday() != 6 or now.hour < 20:
            return None
        week = now.date().isoformat()
        with self.lock:
            if self.data.get("weekly_told") == week:
                return None
            self.data["weekly_told"] = week
            self._save()
        today = now.date()
        this = self.totals(today - datetime.timedelta(days=6), today)
        last = self.totals(today - datetime.timedelta(days=13), today - datetime.timedelta(days=7))
        total, before = sum(this.values()), sum(last.values())
        if not total:
            return None
        top = max(this.items(), key=lambda kv: kv[1])
        text = f"这星期插座一共用了 {total:.1f} 度电,约 {money(total)};最耗电的是 {top[0]}({top[1]:.1f} 度)。"
        if before:
            change = (total - before) / before * 100
            text += f"比上星期{'多' if change > 0 else '少'}了 {abs(change):.0f}%。"
        return text

    def state(self) -> dict:
        today = self.clock().date()
        month = self.totals(today.replace(day=1), today)
        for name, info in self.data["now"].items():
            month[name] = max(month.get(name, 0), info.get("month", 0))
        week = self.totals(today - datetime.timedelta(days=today.weekday()), today)
        devices = [{"name": n, "power": i.get("power", 0), "on": i.get("on"), "today": self.data["days"].get(today.isoformat(), {}).get(n, 0),
                    "week": round(week.get(n, 0), 2), "month": round(month.get(n, 0), 2), "time": i.get("time", "")}
                   for n, i in self.data["now"].items()]
        devices.sort(key=lambda d: -d["month"])
        return {"devices": devices, "rate": rate(), "month_total": round(sum(month.values()), 2),
                "month_cost": round(sum(month.values()) * rate(), 2)}


class Commands:
    def __init__(self, meter: EnergyMeter) -> None:
        self.meter = meter

    def handle(self, text: str) -> str | None:
        found = ASK.match(str(text or "").strip())
        if not found:
            return None
        return self.meter.describe(found.group(3) or "")
