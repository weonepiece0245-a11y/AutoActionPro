"""星期五按时主动说的事(不问 Claude,不花钱):下雨出门提醒、每周花费报告。

做没做过记在 friday_alerts.json,重开星期五也不会重复说。
"""

from __future__ import annotations

import datetime
import json
import os
from pathlib import Path
from typing import Callable

RAIN_TIMES = ["07:00", "07:30", "08:00", "08:30", "09:00"]
DEFAULT_RAIN_TIME = "08:00"
RAIN_THRESHOLD = 60  # 降雨概率到这么多才提醒
RAIN_UNTIL_HOUR = 21  # 看到晚上 9 点
RAIN_LATEST_HOUR = 18  # 电脑开得晚:过了下午 6 点就不补提醒了
WEEKLY_HOUR = 21  # 星期天晚上 9 点后做每周报告

Clock = Callable[[], datetime.datetime]


def rain_time() -> str:
    """几点提醒;"off" 表示不提醒。"""
    value = os.environ.get("FRIDAY_RAIN_ALERT", DEFAULT_RAIN_TIME).strip().lower()
    return value if value == "off" or value in RAIN_TIMES else DEFAULT_RAIN_TIME


def weekly_enabled() -> bool:
    return os.environ.get("FRIDAY_WEEKLY_REPORT", "on") != "off"


def rain_place() -> str:
    return os.environ.get("FRIDAY_WEATHER_PLACE", "").strip() or "吉隆坡"


class AlertState:
    def __init__(self, path: str | os.PathLike) -> None:
        self.path = Path(path)
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        self.data = data if isinstance(data, dict) else {}

    def get(self, key: str) -> str:
        return str(self.data.get(key, ""))

    def set(self, key: str, value: str) -> None:
        self.data[key] = value
        try:
            self.path.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass


# ======================== 下雨提醒 ========================


def rain_due(state: AlertState, now: datetime.datetime) -> bool:
    when = rain_time()
    if when == "off" or state.get("rain") == now.strftime("%Y-%m-%d"):
        return False
    hour, minute = map(int, when.split(":"))
    return (now.hour, now.minute) >= (hour, minute) and now.hour < RAIN_LATEST_HOUR


def _spans(hours: list[tuple[int, int, str]]) -> list[tuple[int, int, int, str]]:
    """连续的下雨小时并成一段:(开始, 结束, 最高概率, 天气)。"""
    spans: list[list] = []
    for hour, chance, kind in hours:
        if spans and spans[-1][1] == hour:
            spans[-1][1] = hour + 1
            if chance > spans[-1][2]:
                spans[-1][2], spans[-1][3] = chance, kind
        else:
            spans.append([hour, hour + 1, chance, kind])
    return [tuple(s) for s in spans]


def rain_message(data: dict, now: datetime.datetime, name: str, place: str,
                 threshold: int = RAIN_THRESHOLD) -> str | None:
    """今天接下来会下雨就返回提醒的话;不会下就返回 None。data 是 Open-Meteo 的预报。"""
    import friday_weather

    hourly = data.get("hourly") or {}
    today = now.strftime("%Y-%m-%d")
    wet = []
    for i, t in enumerate(hourly.get("time") or []):
        if not t.startswith(today):
            continue
        hour = int(t[11:13])
        if hour < now.hour or hour >= RAIN_UNTIL_HOUR:
            continue
        chance = hourly.get("precipitation_probability", [None] * (i + 1))[i]
        if chance is None or chance < threshold:
            continue
        wet.append((hour, int(chance), friday_weather.describe_code(hourly.get("weather_code", [None] * (i + 1))[i])))
    if not wet:
        return None
    parts = [f"{a}点到{b}点{kind}(降雨概率 {c}%)" for a, b, c, kind in _spans(wet)]
    return f"{name},{place}今天 " + "、".join(parts) + " 可能会下雨,出门记得带伞。"


def check_rain(state: AlertState, now: datetime.datetime, name: str, fetch=None) -> str | None:
    """到点了就看一次天气;要下雨就返回提醒的话(看过了今天就不再看)。"""
    import friday_weather

    if not rain_due(state, now):
        return None
    place = friday_weather.find_place(rain_place(), fetch or friday_weather.fetch_json)
    data = friday_weather.forecast(place, 1, fetch or friday_weather.fetch_json)
    state.set("rain", now.strftime("%Y-%m-%d"))
    return rain_message(data, now, name, place["name"], RAIN_THRESHOLD)


# ======================== 每周报告 ========================


def report_week(state: AlertState, now: datetime.datetime) -> tuple[datetime.date, datetime.date] | None:
    """该报告哪一周(星期一, 星期天):星期天晚上 9 点后报这一周;错过了,下星期三前补上一周。"""
    if not weekly_enabled():
        return None
    today = now.date()
    monday = today - datetime.timedelta(days=today.weekday())
    if today.weekday() == 6 and now.hour >= WEEKLY_HOUR:
        week = (monday, today)
    elif today.weekday() <= 2:
        week = (monday - datetime.timedelta(days=7), monday - datetime.timedelta(days=1))
    else:
        return None
    return None if state.get("weekly") == week[0].isoformat() else week


def money_myr(usd: float, rate: float) -> str:
    return f"RM {usd * rate:.2f}"


def weekly_report(meter, ledger, week: tuple[datetime.date, datetime.date], name: str,
                  budget_myr: float = 0.0) -> str:
    first, last = (d.isoformat() for d in week)
    lines = [f"{name},这是这周({week[0].month}/{week[0].day}~{week[1].month}/{week[1].day})的花费报告。"]
    if meter is not None:
        usage = meter.between(first, last)
        rate = meter.rate
        before = (week[0] - datetime.timedelta(days=7)).isoformat(), (week[0] - datetime.timedelta(days=1)).isoformat()
        prev = meter.between(*before)
        text = f"星期五这周花了 {money_myr(usage['usd'], rate)},问了 Claude {usage['calls']} 次"
        if usage["local"]:
            text += f",另外有 {usage['local']} 次简单指令在电脑上直接办好,没花钱"
        lines.append(text + "。")
        if usage["models"]:
            parts = "、".join(f"{m} {money_myr(v, rate)}" for m, v in sorted(usage["models"].items(), key=lambda kv: -kv[1]))
            lines.append(f"按模型:{parts}。")
        if usage["days"]:
            day, usd = max(usage["days"].items(), key=lambda kv: kv[1])
            weekday = "一二三四五六日"[datetime.date.fromisoformat(day).weekday()]
            lines.append(f"花得最多的是星期{weekday},{money_myr(usd, rate)}。")
        if prev["usd"] > 0.0005:
            diff = usage["usd"] - prev["usd"]
            lines.append(f"比上周{'多' if diff > 0 else '少'} {money_myr(abs(diff), rate)}。")
        if budget_myr:
            month = meter.month_myr()
            lines.append(f"这个月到现在 RM {month:.2f},是预算 RM {budget_myr:.0f} 的 {month / budget_myr:.0%}。")
        opus = usage["models"].get("Opus", 0.0)
        if usage["usd"] and opus / usage["usd"] > 0.7:
            lines.append("大部分钱花在 Opus 上;日常的事可以在齿轮设定里改成“自动选模型”,能省一些。")
    if ledger is not None:
        entries = [e for e in ledger.entries if first <= e["date"] <= last]
        if entries:
            total = sum(e["amount"] for e in entries)
            by_category: dict[str, float] = {}
            for e in entries:
                by_category[e["category"]] = by_category.get(e["category"], 0) + e["amount"]
            top = "、".join(f"{c} RM {v:.2f}" for c, v in sorted(by_category.items(), key=lambda kv: -kv[1])[:3])
            lines.append(f"你自己记的账:这周 {len(entries)} 笔,共 RM {total:,.2f},最多的是 {top}。")
    return "".join(lines)


def month_report(meter, today: datetime.date, budget=None) -> str:
    """这个月的钱花在哪里(不问 Claude):总数、预算、花得最多的几天、各模型、照这个速度月底多少。"""
    import calendar

    first = today.replace(day=1)
    usage = meter.between(first.isoformat(), today.isoformat())
    rate = meter.rate
    estimate = usage["usd"] * rate
    lines = [f"这个月({today.month} 月 1 日到今天)星期五用 Claude 花了约 RM {estimate:.2f}。"]
    if budget is not None:
        spent, source = budget.month_myr()
        if source == "console":
            lines.append(f"Claude Console 上的实际花费是 RM {spent:.2f},以 Console 为准。")
        if budget.budget_myr:
            lines.append(f"预算 RM {budget.budget_myr:g},已经用了 {spent / budget.budget_myr:.0%}。")
        estimate = max(estimate, spent)
    if usage["days"]:
        top = sorted(usage["days"].items(), key=lambda kv: -kv[1])[:5]
        lines.append("花得最多的几天:" + "、".join(f"{d[5:7].lstrip('0')}/{d[8:].lstrip('0')} RM {v * rate:.2f}"
                                                  for d, v in top) + "。")
    if usage["models"]:
        total = sum(usage["models"].values()) or 1
        parts = "、".join(f"{m} RM {v * rate:.2f}({v / total:.0%})"
                         for m, v in sorted(usage["models"].items(), key=lambda kv: -kv[1]))
        lines.append(f"按模型:{parts}。(分模型的记录是从 11/10 的更新之后才开始的)")
    else:
        lines.append("分模型的记录从 11/10 的更新之后才开始,之后再问就看得到。")
    if usage["calls"] or usage["local"]:
        lines.append(f"问了 Claude {usage['calls']} 次" + (f",另外有 {usage['local']} 次简单指令在电脑上直接办好,没花钱"
                                                     if usage["local"] else "") + "(这两项也是 11/10 起才开始记)。")
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    if today.day >= 3:
        projected = estimate / today.day * days_in_month
        lines.append(f"照这个月到现在的速度,整个月大约 RM {projected:.0f}。")
    opus = usage["models"].get("Opus", 0.0)
    if usage["usd"] and opus / usage["usd"] > 0.6:
        lines.append("大部分钱花在 Opus 上:齿轮设定里选“自动选模型”,日常的事会改用便宜一半的 Sonnet。")
    return "".join(lines)


def key_expiry_message(state: AlertState, today: datetime.date, name: str) -> str | None:
    """API 钥匙快到期:提前 3 天、1 天和当天各提醒一次(到期日在齿轮设定里填)。"""
    raw = os.environ.get("FRIDAY_KEY_EXPIRES", "").strip()
    try:
        expires = datetime.date.fromisoformat(raw)
    except ValueError:
        return None
    days = (expires - today).days
    if days not in (3, 1, 0) and days >= 0 or days < -1:
        return None
    key = f"key_{expires.isoformat()}_{days}"
    if state.get("key_expiry") == key:
        return None
    state.set("key_expiry", key)
    if days < 0:
        return f"{name},Claude 钥匙昨天已经过期了,星期五可能已经没办法回答。去 Console 建一把新的,换进环境变量 ANTHROPIC_API_KEY。"
    when = "今天" if days == 0 else ("明天" if days == 1 else f"{days} 天后({expires.month}/{expires.day})")
    return (f"{name},星期五用的 Claude 钥匙{when}到期。去 Console 建一把新的,换进 Windows 环境变量 ANTHROPIC_API_KEY,"
            "再到齿轮设定 → 其他 更新到期日。")


def low_credit_message(state: AlertState, today: datetime.date, budget, name: str, below_usd: float = 3.0) -> str | None:
    """余额快用完(低于 US$3):一天提醒一次。余额是按你在齿轮设定里填的充值金额估算的。"""
    if budget is None:
        return None
    remaining = budget.remaining_usd()
    if remaining is None or remaining >= below_usd or state.get("low_credit") == today.isoformat():
        return None
    state.set("low_credit", today.isoformat())
    return (f"{name},Claude 账户余额大约只剩 US${max(remaining, 0):.2f},快用完了。去 Console 的“账单与付款”充值,"
            "充完在齿轮设定 → 预算和余额 里更新充值金额。")

