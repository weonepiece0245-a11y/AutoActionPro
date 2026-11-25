"""查天气:用 Open-Meteo(免费、不用钥匙),按当地时区给现在的天气、逐小时和每天的预报。

比上网搜准:搜索结果常是旧网页,时间也可能对不上;这里直接拿最新的预报数据。
"""

from __future__ import annotations

import datetime
import json
import urllib.parse
import urllib.request
from typing import Callable

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
DEFAULT_PLACE = "吉隆坡"

# 常用地名(中文找不到时用英文再找;吉隆坡直接用坐标,最快)
KNOWN_PLACES = {
    "吉隆坡": ("吉隆坡", "马来西亚", 3.1390, 101.6869, "Asia/Kuala_Lumpur"),
    "kl": ("吉隆坡", "马来西亚", 3.1390, 101.6869, "Asia/Kuala_Lumpur"),
    "kuala lumpur": ("吉隆坡", "马来西亚", 3.1390, 101.6869, "Asia/Kuala_Lumpur"),
}
ALIASES = {
    "槟城": "George Town", "乔治市": "George Town", "新山": "Johor Bahru", "马六甲": "Malacca", "怡保": "Ipoh",
    "八打灵再也": "Petaling Jaya", "梳邦再也": "Subang Jaya", "莎阿南": "Shah Alam", "布城": "Putrajaya",
    "云顶": "Genting Highlands", "金马伦": "Cameron Highlands", "亚庇": "Kota Kinabalu", "古晋": "Kuching",
    "关丹": "Kuantan", "兰卡威": "Langkawi", "新加坡": "Singapore", "曼谷": "Bangkok", "台北": "Taipei",
    "香港": "Hong Kong", "东京": "Tokyo", "首尔": "Seoul", "雅加达": "Jakarta",
}

WEATHER_CODES = {
    0: "晴", 1: "大致晴朗", 2: "局部多云", 3: "阴天", 45: "有雾", 48: "冻雾",
    51: "小毛毛雨", 53: "毛毛雨", 55: "大毛毛雨", 56: "冻毛毛雨", 57: "强冻毛毛雨",
    61: "小雨", 63: "中雨", 65: "大雨", 66: "冻雨", 67: "强冻雨",
    71: "小雪", 73: "中雪", 75: "大雪", 77: "雪粒",
    80: "小阵雨", 81: "阵雨", 82: "强阵雨", 85: "小阵雪", 86: "大阵雪",
    95: "雷阵雨", 96: "雷阵雨伴冰雹", 99: "强雷阵雨伴冰雹",
}
WEEKDAYS = "一二三四五六日"

Fetch = Callable[[str], dict]


def fetch_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "friday-assistant"})
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def describe_code(code) -> str:
    try:
        return WEATHER_CODES.get(int(code), "天气不明")
    except (TypeError, ValueError):
        return "天气不明"


def find_place(name: str, fetch: Fetch = fetch_json) -> dict:
    """地名 -> {name, country, lat, lon, timezone};找不到抛 ValueError。"""
    name = (name or DEFAULT_PLACE).strip()
    known = KNOWN_PLACES.get(name.lower())
    if known:
        return dict(zip(("name", "country", "lat", "lon", "timezone"), known))
    for query, language in ((name, "zh"), (ALIASES.get(name, ""), "zh"), (name, "en")):
        if not query:
            continue
        params = urllib.parse.urlencode({"name": query, "count": 1, "language": language, "format": "json"})
        results = fetch(f"{GEOCODE_URL}?{params}").get("results") or []
        if results:
            r = results[0]
            return {"name": r.get("name", name), "country": r.get("country", ""), "lat": r["latitude"],
                    "lon": r["longitude"], "timezone": r.get("timezone") or "auto"}
    raise ValueError(f"找不到“{name}”这个地方,换个写法试试(例如英文名)")


def forecast(place: dict, days: int = 1, fetch: Fetch = fetch_json) -> dict:
    params = {
        "latitude": place["lat"], "longitude": place["lon"], "timezone": place["timezone"],
        "forecast_days": max(1, min(int(days) + 1, 8)),  # 多拿一天,半夜问“接下来几小时”也够
        "current": "temperature_2m,apparent_temperature,relative_humidity_2m,precipitation,weather_code,wind_speed_10m",
        "hourly": "temperature_2m,precipitation_probability,precipitation,weather_code",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,"
                 "precipitation_sum,uv_index_max,sunrise,sunset",
    }
    return fetch(f"{FORECAST_URL}?{urllib.parse.urlencode(params)}")


def _day_label(day: str) -> str:
    try:
        date = datetime.date.fromisoformat(day)
    except ValueError:
        return day
    return f"{date.month}月{date.day}日 星期{WEEKDAYS[date.weekday()]}"


def describe(place: dict, data: dict, hours: int = 12, days: int = 1) -> str:
    current = data.get("current") or {}
    tz = data.get("timezone") or place.get("timezone", "")
    where = place["name"] + (f"({place['country']})" if place.get("country") else "")
    now_text = str(current.get("time", "")).replace("T", " ")
    lines = [f"{where},当地时间 {now_text}(时区 {tz}):"]
    if current:
        lines.append(
            f"现在 {describe_code(current.get('weather_code'))},{current.get('temperature_2m')}°C,"
            f"体感 {current.get('apparent_temperature')}°C,湿度 {current.get('relative_humidity_2m')}%,"
            f"风速 {current.get('wind_speed_10m')} km/h,当前降水 {current.get('precipitation')} mm")
    hourly = data.get("hourly") or {}
    times = hourly.get("time") or []
    now_hour = str(current.get("time", ""))[:13]
    start = next((i for i, t in enumerate(times) if t[:13] >= now_hour), 0) if now_hour else 0
    picked = range(start, min(start + max(1, min(int(hours), 48)), len(times)))
    if picked:
        lines.append("逐小时:")
        for i in picked:
            t = times[i]
            lines.append(
                f"- {t[5:10]} {t[11:16]} {describe_code(hourly['weather_code'][i])} {hourly['temperature_2m'][i]}°C"
                f" 降雨概率 {hourly['precipitation_probability'][i]}% 雨量 {hourly['precipitation'][i]} mm")
    daily = data.get("daily") or {}
    for i, day in enumerate((daily.get("time") or [])[:max(1, min(int(days), 7))]):
        if i == 0:
            lines.append("每天:")
        lines.append(
            f"- {_day_label(day)} {describe_code(daily['weather_code'][i])} "
            f"{daily['temperature_2m_min'][i]}~{daily['temperature_2m_max'][i]}°C "
            f"降雨概率 {daily['precipitation_probability_max'][i]}% 雨量 {daily['precipitation_sum'][i]} mm "
            f"紫外线 {daily['uv_index_max'][i]} 日出 {str(daily['sunrise'][i])[11:16]} 日落 {str(daily['sunset'][i])[11:16]}")
    lines.append("(数据来源 Open-Meteo)")
    return "\n".join(lines)


def weather(location: str = "", days: int = 1, hours: int = 12, fetch: Fetch = fetch_json) -> str:
    place = find_place(location or DEFAULT_PLACE, fetch)
    return describe(place, forecast(place, days, fetch), hours=hours, days=days)
