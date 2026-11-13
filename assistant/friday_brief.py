"""早安简报:每天早上第一次坐到电脑前(或说“早安”),一次说完今天要知道的事(不花钱)。

天气、日历活动、提醒、待办、生日、账单、新邮件、昨天花了多少。哪一项拿不到就跳过,不会卡住。
每天只自动说一次,记在 friday_alerts.json。
"""

from __future__ import annotations

import concurrent.futures
import datetime
import os
from typing import Callable

FROM_HOUR = 5  # 早上 5 点到中午 12 点之间,第一次在电脑前就说
UNTIL_HOUR = 12
PRESENT_SECONDS = 120  # 最近 2 分钟有动键盘鼠标才算坐在电脑前
SOURCE_TIMEOUT = 12
WEEKDAYS = "一二三四五六日"

Source = Callable[[datetime.datetime], "str | None"]


def enabled() -> bool:
    return os.environ.get("FRIDAY_BRIEF", "on") != "off"


def greeting(name: str, now: datetime.datetime) -> str:
    hello = "早安" if now.hour < 11 else "中午好" if now.hour < 13 else "下午好" if now.hour < 18 else "晚上好"
    return f"{hello},{name}。今天是 {now.month} 月 {now.day} 日 星期{WEEKDAYS[now.weekday()]}。"


# ======================== 各项 ========================


def weather_line(place: str, data: dict, now: datetime.datetime, threshold: int = 60) -> str | None:
    import friday_alerts
    import friday_weather

    daily = data.get("daily") or {}
    today = now.strftime("%Y-%m-%d")
    days = daily.get("time") or []
    if today not in days:
        return None
    i = days.index(today)
    text = (f"{place}今天{friday_weather.describe_code(daily['weather_code'][i])},"
            f"{round(daily['temperature_2m_min'][i])}~{round(daily['temperature_2m_max'][i])}°C")
    hourly = data.get("hourly") or {}
    wet = []
    for j, t in enumerate(hourly.get("time") or []):
        if not t.startswith(today) or int(t[11:13]) < now.hour or int(t[11:13]) >= friday_alerts.RAIN_UNTIL_HOUR:
            continue
        chance = (hourly.get("precipitation_probability") or [None] * (j + 1))[j]
        if chance is not None and chance >= threshold:
            wet.append((int(t[11:13]), int(chance), friday_weather.describe_code(hourly["weather_code"][j])))
    if wet:
        spans = friday_alerts._spans(wet)
        text += ";" + "、".join(f"{a}点到{b}点可能下雨({c}%)" for a, b, c, _ in spans) + ",出门带伞"
    else:
        uv = (daily.get("uv_index_max") or [None] * (i + 1))[i]
        if uv is not None and uv >= 8:
            text += f",紫外线很强({round(uv)}),出门记得防晒"
    return text + "。"


def events_line(events: list[dict], now: datetime.datetime) -> str | None:
    today = now.date()
    timed, all_day = [], []
    for event in events:
        if event.get("status") == "cancelled":
            continue
        title = str(event.get("summary") or "(没有标题)").strip()[:40]
        start = event.get("start") or {}
        if start.get("dateTime"):
            try:
                when = datetime.datetime.fromisoformat(start["dateTime"].replace("Z", "+00:00"))
            except ValueError:
                continue
            if when.tzinfo is not None and now.tzinfo is None:
                when = when.astimezone().replace(tzinfo=None)
            if when.date() == today:
                timed.append((when, title))
        elif start.get("date") == today.isoformat():
            all_day.append(title)
    if not timed and not all_day:
        return "今天日历上没有活动。"
    parts = [f"{w:%H:%M} {t}" for w, t in sorted(timed)] + [f"全天:{t}" for t in all_day]
    return f"日历上今天有 {len(parts)} 个活动:" + "、".join(parts[:6]) + ("……" if len(parts) > 6 else "") + "。"


def reminders_line(items: list[dict], now: datetime.datetime) -> str | None:
    today = now.strftime("%Y-%m-%d")
    found = [f"{r['time'][11:16]} {r['message']}" for r in items
             if str(r.get("time", ""))[:10] == today and r["time"] >= now.strftime("%Y-%m-%d %H:%M")]
    if not found:
        return None
    return f"今天的提醒:" + "、".join(found[:5]) + ("……" if len(found) > 5 else "") + "。"


def todos_line(items: list[dict], now: datetime.datetime) -> str | None:
    """items: [{"text", "due"}](没做完的)。"""
    today = now.strftime("%Y-%m-%d")
    due_today = [t["text"] for t in items if t.get("due") == today]
    late = [t["text"] for t in items if t.get("due") and t["due"] < today]
    if not items:
        return None
    parts = []
    if due_today:
        parts.append("今天要完成:" + "、".join(due_today[:4]))
    if late:
        parts.append(f"已经过期 {len(late)} 个(" + "、".join(late[:3]) + ")")
    if not parts:
        return f"待办清单上有 {len(items)} 件事,今天没有到期的。"
    return "待办:" + ";".join(parts) + "。"


def mail_line(mails: list[dict]) -> str | None:
    """mails: 昨天以来没读的 [{"from", "subject", "important"}]。"""
    if not mails:
        return None
    important = [m for m in mails if m.get("important")]
    text = f"收件箱有 {len(mails)} 封新的未读邮件"
    if important:
        text += ",重要的:" + "、".join(f"{m['from']}“{m['subject'][:30]}”" for m in important[:3])
    return text + "。"


def spending_line(ledger_entries: list[dict], ai_myr: float | None, now: datetime.datetime) -> str | None:
    yesterday = (now.date() - datetime.timedelta(days=1)).isoformat()
    entries = [e for e in ledger_entries if e.get("date") == yesterday]
    parts = []
    if entries:
        parts.append(f"你记的账 RM {sum(e['amount'] for e in entries):,.2f}({len(entries)} 笔)")
    if ai_myr is not None and ai_myr >= 0.005:
        parts.append(f"星期五用 Claude 花了约 RM {ai_myr:.2f}")
    return ("昨天" + ",".join(parts) + "。") if parts else None


# ======================== 简报 ========================


class MorningBrief:
    def __init__(self, state, sources: list[Source], clock: Callable[[], datetime.datetime] = datetime.datetime.now,
                 timeout: float = SOURCE_TIMEOUT) -> None:
        self.state = state  # friday_alerts.AlertState
        self.sources = sources
        self.clock = clock
        self.timeout = timeout

    def compose(self, name: str) -> str:
        now = self.clock()
        lines = [greeting(name, now)]
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(self.sources)))
        futures = [pool.submit(source, now) for source in self.sources]
        for future in futures:  # 按顺序收,哪一项太慢或出错就跳过
            try:
                line = future.result(timeout=self.timeout)
            except Exception:
                continue
            if line:
                lines.append(line)
        pool.shutdown(wait=False, cancel_futures=True)
        if len(lines) == 1:
            lines.append("今天没有特别的安排。")
        return "\n".join(lines)

    def due(self, idle: float | None) -> bool:
        now = self.clock()
        if not enabled() or self.state.get("brief") == now.strftime("%Y-%m-%d"):
            return False
        if not FROM_HOUR <= now.hour < UNTIL_HOUR:
            return False
        return idle is not None and idle < PRESENT_SECONDS

    def mark(self) -> None:
        self.state.set("brief", self.clock().strftime("%Y-%m-%d"))
