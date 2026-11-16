"""日历事前提醒:Google 日历上的约会、会议,开始前 15 分钟(可以改)主动提醒你,不问 Claude,不花钱。

全天的活动、星期五自己放进日历的提醒(标题前有 ⏰)、你已经拒绝的邀请,都不提醒。
有地点的活动,顺便看看那时候会不会下雨。
"""

from __future__ import annotations

import datetime
import os
from typing import Callable

LEADS = (0, 10, 15, 30, 60)
DEFAULT_LEAD = 15
FETCH_EVERY = 5 * 60  # 秒:每 5 分钟问一次 Google 日历
RAIN_THRESHOLD = 50


def lead_minutes() -> int:
    try:
        value = int(os.environ.get("FRIDAY_EVENT_LEAD", DEFAULT_LEAD))
    except ValueError:
        return DEFAULT_LEAD
    return value if value in LEADS else DEFAULT_LEAD


def _start(event: dict) -> datetime.datetime | None:
    raw = (event.get("start") or {}).get("dateTime")
    if not raw:
        return None  # 全天的活动
    try:
        return datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def worth_reminding(event: dict) -> bool:
    if event.get("status") == "cancelled" or _start(event) is None:
        return False
    if (event.get("extendedProperties") or {}).get("private", {}).get("friday_reminder") is not None:
        return False  # 星期五自己放进去的提醒:本来就会响
    if str(event.get("summary", "")).startswith("⏰"):
        return False
    for person in event.get("attendees") or []:
        if person.get("self") and person.get("responseStatus") == "declined":
            return False
    return True


class EventReminder:
    """每分钟 check() 一次;到了该提醒的时候返回要说的话。"""

    def __init__(self, events: Callable[[datetime.datetime, datetime.datetime], list[dict]], state,
                 clock: Callable[[], datetime.datetime] = lambda: datetime.datetime.now().astimezone(),
                 rain: Callable[[datetime.datetime], int | None] | None = None) -> None:
        self.events = events  # (开始, 结束) -> 日历上的活动
        self.state = state  # friday_alerts.AlertState:提醒过哪些
        self.clock = clock
        self.rain = rain  # 某个时间的降雨概率(%)
        self.cache: list[dict] = []
        self.fetched_at: datetime.datetime | None = None

    def _fetch(self, now: datetime.datetime) -> None:
        if self.fetched_at is not None and (now - self.fetched_at).total_seconds() < FETCH_EVERY:
            return
        self.cache = [e for e in self.events(now, now + datetime.timedelta(minutes=max(LEADS) + 10)) if worth_reminding(e)]
        self.fetched_at = now

    def check(self, name: str) -> list[str]:
        lead = lead_minutes()
        if not lead:
            return []
        now = self.clock()
        self._fetch(now)
        done = self.state.data.get("events_done") or {}
        said = []
        for event in self.cache:
            start = _start(event)
            key = f"{event.get('id', '')}@{start.isoformat()}"
            minutes = (start - now).total_seconds() / 60
            if key in done or minutes < 0 or minutes > lead:
                continue
            done[key] = now.strftime("%Y-%m-%d")
            said.append(self._text(name, event, start.astimezone(now.tzinfo), minutes))
        if said:  # 记下提醒过哪些(只留最近两天的)
            cutoff = (now - datetime.timedelta(days=2)).strftime("%Y-%m-%d")
            self.state.set("events_done", {k: v for k, v in done.items() if v >= cutoff})
        return said

    def _text(self, name: str, event: dict, start: datetime.datetime, minutes: float) -> str:
        local = start
        when = f"{round(minutes)} 分钟后({local:%H:%M})" if minutes >= 1 else f"现在({local:%H:%M})"
        text = f"{name},{when}有「{event.get('summary') or '(没有标题)'}」"
        if event.get("location"):
            text += f",地点:{event['location']}"
        text += "。"
        if event.get("location") and self.rain is not None:
            try:
                chance = self.rain(local)
            except Exception:
                chance = None
            if chance is not None and chance >= RAIN_THRESHOLD:
                text += f"那时候可能下雨(降雨概率 {chance}%),出门记得带伞。"
        if event.get("hangoutLink"):
            text += "有 Google Meet 链接,在日历里点开就能进。"
        return text


def rain_at(fetch=None) -> Callable[[datetime.datetime], int | None]:
    """默认城市某个小时的降雨概率(Open-Meteo,一小时最多查一次)。"""
    import friday_alerts
    import friday_weather

    cache: dict = {}

    def chance(when: datetime.datetime) -> int | None:
        hour = when.strftime("%Y-%m-%dT%H")
        if cache.get("hour") != datetime.datetime.now().strftime("%Y-%m-%dT%H"):
            place = friday_weather.find_place(friday_alerts.rain_place(), fetch or friday_weather.fetch_json)
            cache["data"] = friday_weather.forecast(place, 1, fetch or friday_weather.fetch_json)
            cache["hour"] = datetime.datetime.now().strftime("%Y-%m-%dT%H")
        hourly = cache["data"].get("hourly") or {}
        for t, p in zip(hourly.get("time") or [], hourly.get("precipitation_probability") or []):
            if t.startswith(hour):
                return int(p) if p is not None else None
        return None

    return chance
