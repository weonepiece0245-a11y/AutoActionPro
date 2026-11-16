"""主画面的仪表板:天气、今天的行程、Spotify 正在放什么、电脑状态、家里的灯(不花钱)。

窗口开着、看得到时,界面每 10 秒来拿一次;每一块各自记一段时间(天气 15 分钟、行程 5 分钟、
音乐 5 秒、电脑 10 秒、家里 1 分钟),不会一直去问外面的服务。哪一块没设好就不显示。
"""

from __future__ import annotations

import datetime
import os
import threading
import time
from typing import Callable

TTL = {"weather": 15 * 60, "today": 5 * 60, "music": 5, "pc": 10, "home": 60}


# ======================== 电脑用量(Windows) ========================


class Usage:
    """整台电脑的 CPU、内存用了多少(%)。"""

    def __init__(self) -> None:
        self.prev: tuple[int, int] | None = None

    def read(self) -> dict | None:
        if os.name != "nt":
            return None
        import ctypes
        from ctypes import wintypes

        idle, kernel, user = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
        if not ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
            return None
        value = lambda t: (t.dwHighDateTime << 32) | t.dwLowDateTime  # noqa: E731
        busy_total = (value(kernel) + value(user), value(idle))
        cpu = None
        if self.prev is not None:
            total = busy_total[0] - self.prev[0]
            idle_delta = busy_total[1] - self.prev[1]
            cpu = round(max(0.0, min(100.0, (total - idle_delta) / total * 100)), 0) if total > 0 else None
        self.prev = busy_total

        class Memory(ctypes.Structure):
            _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        memory = Memory()
        memory.dwLength = ctypes.sizeof(memory)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory))
        return {"cpu": cpu, "mem": int(memory.dwMemoryLoad),
                "mem_total": round(memory.ullTotalPhys / 1024 ** 3), "mem_used": round(
                    (memory.ullTotalPhys - memory.ullAvailPhys) / 1024 ** 3, 1)}


# ======================== 各一块 ========================


def weather_card(place: str, fetch=None) -> dict:
    import friday_weather

    kwargs = {"fetch": fetch} if fetch else {}
    where = friday_weather.find_place(place, **kwargs)
    data = friday_weather.forecast(where, 1, **kwargs)
    current = data.get("current") or {}
    daily = data.get("daily") or {}
    hourly = data.get("hourly") or {}
    now_hour = str(current.get("time", ""))[:13]
    times = hourly.get("time") or []
    start = next((i for i, t in enumerate(times) if t[:13] >= now_hour), 0) if now_hour else 0
    hours = []
    for i in range(start + 1, min(start + 7, len(times)), 1):
        hours.append({"time": times[i][11:16], "temp": hourly["temperature_2m"][i],
                      "rain": hourly["precipitation_probability"][i], "code": hourly["weather_code"][i]})
    first = lambda key: (daily.get(key) or [None])[0]  # noqa: E731
    return {"place": where["name"], "temp": current.get("temperature_2m"), "feels": current.get("apparent_temperature"),
            "code": current.get("weather_code"), "text": friday_weather.describe_code(current.get("weather_code")),
            "high": first("temperature_2m_max"), "low": first("temperature_2m_min"),
            "rain": first("precipitation_probability_max"), "hours": hours}


def today_card(events: Callable[[datetime.datetime, datetime.datetime], list[dict]] | None,
               reminders: list[dict], now: datetime.datetime) -> list[dict]:
    """今天还没过的行程:日历活动 + 星期五的提醒,按时间排。"""
    items = []
    if events is not None:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        local = start.astimezone() if start.tzinfo is None else start
        for e in events(local, local + datetime.timedelta(days=1)) or []:
            when = e.get("start") or {}
            if "dateTime" in when:
                at = datetime.datetime.fromisoformat(when["dateTime"].replace("Z", "+00:00")).astimezone()
                end = (e.get("end") or {}).get("dateTime")
                if end and datetime.datetime.fromisoformat(end.replace("Z", "+00:00")).astimezone() < now.astimezone():
                    continue  # 已经结束了
                items.append({"time": at.strftime("%H:%M"), "title": e.get("summary") or "(无标题)", "kind": "event"})
            else:
                items.append({"time": "全天", "title": e.get("summary") or "(无标题)", "kind": "event"})
    today = now.strftime("%Y-%m-%d")
    for r in reminders:
        when = str(r.get("time", ""))
        if when[:10] == today and when >= now.strftime("%Y-%m-%d %H:%M"):
            items.append({"time": when[11:16], "title": r.get("message", ""), "kind": "reminder"})
    items.sort(key=lambda i: ("" if i["time"] == "全天" else i["time"]))
    return items[:8]


def music_card(spotify) -> dict | None:
    status, data = spotify.api("GET", "/me/player")
    if status != 200 or not data or not data.get("item"):
        return None
    item = data["item"]
    images = (item.get("album") or {}).get("images") or []
    art = min(images, key=lambda i: i.get("width") or 999).get("url") if images else ""
    return {"title": item.get("name", ""), "artist": "、".join(a.get("name", "") for a in item.get("artists") or []),
            "playing": bool(data.get("is_playing")), "art": art if str(art).startswith("https://i.scdn.co/") else "",
            "progress": data.get("progress_ms") or 0, "duration": item.get("duration_ms") or 0,
            "device": (data.get("device") or {}).get("name", "")}


# ======================== 仪表板 ========================


class Dashboard:
    def __init__(self, sources: dict[str, Callable[[], object]], clock: Callable[[], float] = time.monotonic) -> None:
        """sources:{"weather": ..., "today": ..., "music": ..., "pc": ..., "home": ...},每个返回那一块的资料,
        没设好返回 None(那一块就不显示);出错时用上一次的。"""
        self.sources = sources
        self.clock = clock
        self.cache: dict[str, tuple[float, object]] = {}
        self.lock = threading.Lock()
        self.actions: dict[str, Callable[[dict], str]] = {}

    def _get(self, key: str, fresh: bool = False):
        hit = self.cache.get(key)
        if hit and not fresh and self.clock() - hit[0] < TTL.get(key, 30):
            return hit[1]
        try:
            value = self.sources[key]()
        except Exception as e:  # noqa: BLE001
            if hit:
                return hit[1]
            print(f"仪表板“{key}”没拿到:{type(e).__name__}: {e}", flush=True)
            value = None
        self.cache[key] = (self.clock(), value)
        return value

    def data(self, fresh: tuple[str, ...] = ()) -> dict:
        with self.lock:
            return {key: self._get(key, key in fresh) for key in self.sources}

    def act(self, payload: dict) -> dict:
        kind = str(payload.get("action") or "")
        handler = self.actions.get(kind)
        if handler is None:
            return {"error": "不认识的操作"}
        try:
            text = handler(payload)
        except ValueError as e:
            return {"error": str(e)}
        except Exception as e:  # noqa: BLE001
            print(f"仪表板操作“{kind}”出错:{type(e).__name__}: {e}", flush=True)
            return {"error": "没做成,等一下再试"}
        refresh = {"music": ("music",), "light": ("home",)}.get(kind, ())
        return {"ok": True, "text": text or "", "data": self.data(fresh=refresh)}
