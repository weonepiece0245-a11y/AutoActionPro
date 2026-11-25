"""出门提醒:日历上有地点的活动,算好从家里开车过去要多久,该出门前 5 分钟叫你。

- 地点 -> 经纬度:OpenStreetMap 的 Nominatim(一秒最多问一次,查过的记下来,不会一直问);
- 开车要多久:OSRM 公开的路线服务;
- 再加:上下班塞车时间 ×1.4、那时候可能下雨 +10 分钟、找停车位(设定里可以改,默认 10 分钟)。
只会把活动的“地点”和你设定的家的地址送去 OpenStreetMap 查,不送标题、不送别的。
不问 Claude,不花钱。网上的线上会议(Zoom、Meet、链接)不算。
"""

from __future__ import annotations

import datetime
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

LOOK_AHEAD_HOURS = 4  # 看接下来几小时的活动
FETCH_EVERY = 10 * 60  # 秒:多久问一次 Google 日历
WARN_BEFORE = 5  # 该出门前几分钟叫你
PEAK_FACTOR = 1.4
RAIN_MINUTES = 10
RAIN_THRESHOLD = 50
DEFAULT_EXTRA = 10
EXTRA_CHOICES = (0, 5, 10, 15, 20, 30)
MIN_TRAVEL = 5  # 不到 5 分钟的路(同一栋楼、附近)不提醒
USER_AGENT = "friday-personal-assistant/1.0"
ONLINE = re.compile(r"(https?://|www\.|zoom|google\s*meet|meet\.google|teams|webex|skype|discord|线上|網上|网上|online|virtual)", re.I)

Fetch = Callable[[str], object]


def _fetch_json(url: str) -> object:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "zh,en"})
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def home_address() -> str:
    return os.environ.get("FRIDAY_HOME_ADDRESS", "").strip()


def enabled() -> bool:
    return os.environ.get("FRIDAY_DEPART_REMIND", "on").strip().lower() not in ("off", "0", "false", "no")


def extra_minutes() -> int:
    try:
        value = int(os.environ.get("FRIDAY_DEPART_EXTRA", DEFAULT_EXTRA))
    except ValueError:
        return DEFAULT_EXTRA
    return value if value in EXTRA_CHOICES else DEFAULT_EXTRA


def is_place(location: str) -> bool:
    """看起来是一个真的地方(不是线上会议、不是空的)。"""
    location = (location or "").strip()
    return len(location) >= 3 and not ONLINE.search(location)


def is_peak(when: datetime.datetime) -> bool:
    """平日上下班塞车时间(早上 7:00–9:30、傍晚 5:00–7:30)。"""
    if when.weekday() >= 5:
        return False
    minutes = when.hour * 60 + when.minute
    return 7 * 60 <= minutes <= 9 * 60 + 30 or 17 * 60 <= minutes <= 19 * 60 + 30


class Geocoder:
    """地址 -> (纬度, 经度)。查过的存在文件里;一秒最多问一次 Nominatim。"""

    def __init__(self, path: str | os.PathLike, fetch: Fetch = _fetch_json, sleep=time.sleep,
                 clock=time.monotonic) -> None:
        self.path = Path(path)
        self.fetch, self.sleep, self.clock = fetch, sleep, clock
        self.lock = threading.Lock()
        self.last = -10.0
        try:
            self.cache = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(self.cache, dict):
                self.cache = {}
        except (OSError, ValueError):
            self.cache = {}

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.cache, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            pass

    def find(self, address: str) -> tuple[float, float] | None:
        key = " ".join(str(address or "").split())[:300]
        if not key:
            return None
        with self.lock:
            if key in self.cache:
                hit = self.cache[key]
                return tuple(hit) if hit else None
            wait = 1.1 - (self.clock() - self.last)
            if wait > 0:
                self.sleep(wait)
            self.last = self.clock()
            url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(
                {"q": key, "format": "jsonv2", "limit": 1})
            try:
                results = self.fetch(url)
            except Exception:
                return None  # 网络不通:不记下来,下次再试
            hit = None
            if isinstance(results, list) and results:
                try:
                    hit = [float(results[0]["lat"]), float(results[0]["lon"])]
                except (KeyError, TypeError, ValueError):
                    hit = None
            if hit is None and len(self.cache) > 500:
                return None  # 找不到的就不一直记
            self.cache[key] = hit
            if len(self.cache) > 800:
                self.cache = dict(list(self.cache.items())[-500:])
            self._save()
            return tuple(hit) if hit else None


def drive_minutes(origin: tuple[float, float], target: tuple[float, float], fetch: Fetch = _fetch_json) -> float | None:
    """OSRM:不塞车时开车要几分钟;找不到路返回 None。"""
    url = (f"https://router.project-osrm.org/route/v1/driving/"
           f"{origin[1]:.6f},{origin[0]:.6f};{target[1]:.6f},{target[0]:.6f}?overview=false")
    try:
        data = fetch(url)
    except Exception:
        return None
    if not isinstance(data, dict) or data.get("code") != "Ok" or not data.get("routes"):
        return None
    try:
        return float(data["routes"][0]["duration"]) / 60
    except (KeyError, TypeError, ValueError):
        return None


def plan(drive: float, start: datetime.datetime, extra: int, rain: int | None) -> dict:
    """开车分钟 -> {minutes 总共要预留, leave 几点出门, parts 怎么算的}。"""
    parts = [f"开车约 {round(drive)} 分钟"]
    total = drive
    if is_peak(start - datetime.timedelta(minutes=drive)):
        total = drive * PEAK_FACTOR
        parts.append("上下班塞车多算一些")
    if rain is not None and rain >= RAIN_THRESHOLD:
        total += RAIN_MINUTES
        parts.append(f"可能下雨(降雨概率 {rain}%)+{RAIN_MINUTES} 分钟")
    if extra:
        total += extra
        parts.append(f"停车、走路 +{extra} 分钟")
    total = int(round(total))
    return {"minutes": total, "leave": start - datetime.timedelta(minutes=total), "parts": parts}


def _start(event: dict) -> datetime.datetime | None:
    raw = (event.get("start") or {}).get("dateTime")
    if not raw:
        return None
    try:
        return datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


class DepartureReminder:
    """每分钟 check() 一次;该出门了就返回要说的话。"""

    def __init__(self, events: Callable[[datetime.datetime, datetime.datetime], list[dict]], state, geocoder: Geocoder,
                 route: Callable[[tuple, tuple], float | None] = drive_minutes,
                 rain: Callable[[datetime.datetime], int | None] | None = None,
                 clock: Callable[[], datetime.datetime] = lambda: datetime.datetime.now().astimezone()) -> None:
        import friday_events

        self.events, self.state, self.geocoder = events, state, geocoder
        self.route, self.rain, self.clock = route, rain, clock
        self.worth = friday_events.worth_reminding
        self.cache: list[dict] = []
        self.fetched_at: datetime.datetime | None = None
        self.drives: dict[str, float | None] = {}  # 地点 -> 开车分钟(这次开着星期五的期间记着)
        self.problem = ""  # 最近一次算不出来的原因(设定里显示)
        self.upcoming: list[dict] = []  # 接下来要出门的(给界面看)

    def _fetch(self, now: datetime.datetime) -> None:
        if self.fetched_at is not None and (now - self.fetched_at).total_seconds() < FETCH_EVERY:
            return
        events = self.events(now, now + datetime.timedelta(hours=LOOK_AHEAD_HOURS))
        self.cache = [e for e in events if self.worth(e) and is_place(e.get("location", ""))]
        self.fetched_at = now

    def _drive(self, location: str) -> float | None:
        if location in self.drives:
            return self.drives[location]
        home = self.geocoder.find(home_address())
        if home is None:
            self.problem = "找不到你设定的家的地址,写详细一点(例如路名、区、城市)"
            return None
        target = self.geocoder.find(location)
        if target is None:
            self.problem = f"找不到「{location[:40]}」在哪里,日历上的地点写详细一点会比较准"
            self.drives[location] = None
            return None
        minutes = self.route(home, target)
        if minutes is not None:
            self.problem = ""
        self.drives[location] = minutes
        return minutes

    def check(self, name: str) -> list[str]:
        if not enabled() or not home_address():
            return []
        now = self.clock()
        self._fetch(now)
        done = self.state.data.get("depart_done") or {}
        said, upcoming = [], []
        for event in self.cache:
            start = _start(event)
            if start is None or start <= now:
                continue
            location = " ".join(str(event.get("location", "")).split())
            drive = self._drive(location)
            if drive is None or drive < MIN_TRAVEL:
                continue
            rain = None
            if self.rain is not None:
                try:
                    rain = self.rain(start.astimezone(now.tzinfo))
                except Exception:
                    rain = None
            result = plan(drive, start.astimezone(now.tzinfo), extra_minutes(), rain)
            leave = result["leave"]
            upcoming.append({"title": event.get("summary") or "(没有标题)", "leave": leave.strftime("%H:%M"),
                             "start": start.astimezone(now.tzinfo).strftime("%H:%M"), "minutes": result["minutes"]})
            key = f"{event.get('id', '')}@{start.isoformat()}"
            if key in done or (leave - now).total_seconds() > WARN_BEFORE * 60:
                continue
            done[key] = now.strftime("%Y-%m-%d")
            said.append(self._text(name, event, start.astimezone(now.tzinfo), result, now))
        self.upcoming = upcoming
        if said:
            cutoff = (now - datetime.timedelta(days=2)).strftime("%Y-%m-%d")
            self.state.set("depart_done", {k: v for k, v in done.items() if v >= cutoff})
        return said

    @staticmethod
    def _text(name: str, event: dict, start: datetime.datetime, result: dict, now: datetime.datetime) -> str:
        leave = result["leave"]
        late = (now - leave).total_seconds() / 60
        if late >= 1:
            when = f"本来 {leave:%H:%M} 就该出门了(已经晚了 {round(late)} 分钟),现在快点出发"
        else:
            when = f"{leave:%H:%M} 要出门了"
        title = event.get("summary") or "(没有标题)"
        return (f"{name},{when}:{start:%H:%M} 的「{title}」在 {event.get('location', '').strip()[:60]}。"
                f"预留 {result['minutes']} 分钟({'、'.join(result['parts'])})。")
