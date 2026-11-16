"""Apex 英雄:现在是什么地图、下一张图几点换、你的排位分(apexlegendsapi.com 的免费钥匙,不花钱)。

钥匙在 https://portal.apexlegendsapi.com 免费申请,和你的 EA 账号名字一起填进齿轮设定 → 连接的应用。
"""

from __future__ import annotations

import datetime
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

API = "https://api.mozambiquehe.re"
MAPS = {"World's Edge": "世界尽头", "Kings Canyon": "诸王峡谷", "Olympus": "奥林匹斯", "Storm Point": "风暴点",
        "Broken Moon": "残月", "E-District": "E 区", "District": "E 区"}
RANKS = {"Rookie": "菜鸟", "Bronze": "青铜", "Silver": "白银", "Gold": "黄金", "Platinum": "白金", "Diamond": "钻石",
         "Master": "大师", "Apex Predator": "猎杀者", "Unranked": "没有段位"}
DIVISIONS = {1: "I", 2: "II", 3: "III", 4: "IV"}
CACHE_SECONDS = 60


def configured() -> bool:
    return bool(os.environ.get("FRIDAY_APEX_KEY", "").strip())


def fetch_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "Friday"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:  # 网址里有钥匙:不带出去
        if e.code in (401, 403):
            raise ValueError("Apex 的钥匙不对或者过期了,到齿轮设定里重新填") from None
        if e.code == 429:
            raise ValueError("问得太快了,等几秒再问") from None
        if e.code == 404:
            raise ValueError("找不到这个玩家,检查一下齿轮设定里的 EA 名字和平台") from None
        raise ValueError(f"Apex 数据暂时连不上(HTTP {e.code})") from None
    except (urllib.error.URLError, OSError, ValueError):
        raise ValueError("Apex 数据暂时连不上,等一下再试") from None


def map_name(name: str) -> str:
    chinese = MAPS.get(name)
    return f"{chinese}({name})" if chinese else name


def _when(stamp) -> str:
    try:
        return datetime.datetime.fromtimestamp(int(stamp)).strftime("%H:%M")
    except (TypeError, ValueError, OSError):
        return "?"


class Apex:
    def __init__(self, fetch: Callable[[str], dict] = fetch_json, clock: Callable[[], float] = time.time) -> None:
        self.fetch = fetch
        self.clock = clock
        self._maps: tuple[float, dict] | None = None

    def _call(self, path: str, **params) -> dict:
        key = os.environ.get("FRIDAY_APEX_KEY", "").strip()
        if not key:
            raise ValueError("还没连 Apex:到 portal.apexlegendsapi.com 免费申请钥匙,填进齿轮设定 → 连接的应用")
        data = self.fetch(f"{API}/{path}?{urllib.parse.urlencode({'auth': key, **params})}")
        if isinstance(data, dict) and data.get("Error"):
            message = str(data["Error"])
            if "not found" in message.lower():
                raise ValueError("找不到这个玩家,检查一下齿轮设定里的 EA 名字和平台")
            raise ValueError(f"Apex 数据出错了:{message[:80]}")
        return data

    def rotation(self) -> dict:
        if self._maps and self.clock() - self._maps[0] < CACHE_SECONDS:
            return self._maps[1]
        data = self._call("maprotation", version="2")
        self._maps = (self.clock(), data)
        return data

    def maps(self) -> str:
        data = self.rotation()
        parts = []
        for key, label in (("battle_royale", "普通"), ("ranked", "排位")):
            block = data.get(key) or {}
            current, upcoming = block.get("current") or {}, block.get("next") or {}
            if not current.get("map"):
                continue
            text = f"{label}现在是 {map_name(current['map'])}"
            left = current.get("remainingMins")
            if isinstance(left, (int, float)) and left >= 0:
                hours, mins = divmod(int(left), 60)
                text += f",还有 {hours} 小时 {mins} 分钟" if hours else f",还有 {mins} 分钟"
            if upcoming.get("map"):
                text += f",{_when(upcoming.get('start'))} 换成 {map_name(upcoming['map'])}"
            parts.append(text)
        ltm = (data.get("ltm") or {}).get("current") or {}
        if ltm.get("map") and ltm.get("eventName"):
            parts.append(f"限时模式 {ltm['eventName']}:{map_name(ltm['map'])}")
        return ";".join(parts) + "。" if parts else "现在拿不到 Apex 的地图轮换。"

    def player(self, name: str | None = None) -> str:
        name = (name or os.environ.get("FRIDAY_APEX_PLAYER", "")).strip()
        if not name:
            raise ValueError("要先在齿轮设定里填你的 EA 名字")
        platform = os.environ.get("FRIDAY_APEX_PLATFORM", "PC").strip() or "PC"
        data = self._call("bridge", player=name, platform=platform)
        info = data.get("global") or {}
        rank = info.get("rank") or {}
        rank_name = RANKS.get(rank.get("rankName", ""), rank.get("rankName", "?"))
        division = DIVISIONS.get(rank.get("rankDiv")) if rank.get("rankName") not in ("Master", "Apex Predator", "Unranked") else None
        text = f"{info.get('name', name)}:等级 {info.get('level', '?')},排位 {rank_name}" + (f" {division}" if division else "")
        if rank.get("rankScore") is not None:
            text += f"({rank['rankScore']:,} 分)"
        realtime = data.get("realtime") or {}
        selected = ((data.get("legends") or {}).get("selected") or {}).get("LegendName")
        if realtime.get("isOnline"):
            text += ",现在在线" + ("、在游戏里" if realtime.get("isInGame") else "")
        if selected:
            text += f",选的英雄是 {selected}"
        return text + "。"
