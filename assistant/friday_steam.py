"""Steam:最近玩了什么、每个游戏玩了多久、哪些好友在线/在玩什么(不花钱)。

要一把免费的 Steam 网页 API 钥匙(https://steamcommunity.com/dev/apikey)和你的 Steam 个人资料,
填在齿轮设定里(存在 .env,星期五不会说出钥匙)。开游戏不用钥匙,本来就能用。
你的 Steam 个人资料里“游戏详情”和“好友列表”要设成公开,才读得到。
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

API = "https://api.steampowered.com"
STATES = {0: "离线", 1: "在线", 2: "忙碌", 3: "离开", 4: "打盹", 5: "想交易", 6: "想玩游戏"}
CACHE_SECONDS = 5 * 60


def configured() -> bool:
    return bool(os.environ.get("FRIDAY_STEAM_KEY", "").strip() and os.environ.get("FRIDAY_STEAM_ID", "").strip())


def fetch_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "Friday"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:  # 不把带钥匙的网址带出去
        if e.code in (401, 403):
            raise ValueError("Steam 说钥匙不对或被停用了,到齿轮设定里重新填一把") from None
        raise ValueError(f"Steam 暂时连不上(HTTP {e.code}),等一下再试") from None
    except (urllib.error.URLError, OSError, ValueError):
        raise ValueError("Steam 暂时连不上,等一下再试") from None


def hours(minutes: float) -> str:
    if minutes < 60:
        return f"{int(minutes)} 分钟"
    value = minutes / 60
    return f"{value:.0f} 小时" if value >= 10 else f"{value:.1f} 小时"


def _norm(name: str) -> str:
    return re.sub(r"[^0-9a-z一-鿿]", "", name.lower())


class Steam:
    def __init__(self, fetch: Callable[[str], dict] = fetch_json, clock: Callable[[], float] = time.time) -> None:
        self.fetch = fetch
        self.clock = clock
        self.steam_id: str | None = None
        self._owned: tuple[float, list[dict]] | None = None

    def _call(self, path: str, **params) -> dict:
        key = os.environ.get("FRIDAY_STEAM_KEY", "").strip()
        if not key:
            raise ValueError("还没连 Steam:到齿轮设定 → 连接的应用 里填 Steam 网页 API 钥匙和个人资料")
        query = urllib.parse.urlencode({"key": key, "format": "json", **params})
        return self.fetch(f"{API}/{path}?{query}")

    def my_id(self) -> str:
        raw = os.environ.get("FRIDAY_STEAM_ID", "").strip().rstrip("/")
        if not raw:
            raise ValueError("还没填你的 Steam 个人资料(齿轮设定 → 连接的应用)")
        if self.steam_id and self.steam_id[1] == raw:
            return self.steam_id[0]
        found = re.search(r"(7656\d{13})", raw)
        if found:
            steam_id = found.group(1)
        else:
            vanity = raw.split("/id/")[-1].split("/")[0] if "/id/" in raw else raw
            data = self._call("ISteamUser/ResolveVanityURL/v1/", vanityurl=vanity).get("response", {})
            if data.get("success") != 1:
                raise ValueError("找不到这个 Steam 个人资料,填个人资料网址或 17 位数字 ID 试试")
            steam_id = data["steamid"]
        self.steam_id = (steam_id, raw)
        return steam_id

    # ---------- 游戏 ----------
    def recent(self) -> str:
        games = self._call("IPlayerService/GetRecentlyPlayedGames/v1/", steamid=self.my_id()).get("response", {}).get("games", [])
        if not games:
            return "最近两周 Steam 上没有玩游戏的记录(也可能是个人资料的“游戏详情”没设成公开)。"
        games.sort(key=lambda g: -g.get("playtime_2weeks", 0))
        total = sum(g.get("playtime_2weeks", 0) for g in games)
        parts = [f"{g.get('name', '?')} {hours(g.get('playtime_2weeks', 0))}" for g in games[:6]]
        return f"最近两周在 Steam 上玩了 {hours(total)}:" + "、".join(parts) + "。"

    def owned(self) -> list[dict]:
        if self._owned and self.clock() - self._owned[0] < CACHE_SECONDS:
            return self._owned[1]
        games = self._call("IPlayerService/GetOwnedGames/v1/", steamid=self.my_id(), include_appinfo=1,
                           include_played_free_games=1).get("response", {}).get("games", [])
        self._owned = (self.clock(), games)
        return games

    def top(self, count: int = 8) -> str:
        games = sorted(self.owned(), key=lambda g: -g.get("playtime_forever", 0))
        if not games:
            return "读不到你的游戏库(个人资料的“游戏详情”要设成公开)。"
        parts = [f"{g.get('name', '?')} {hours(g.get('playtime_forever', 0))}" for g in games[:count]
                 if g.get("playtime_forever")]
        return f"你的 Steam 有 {len(games)} 个游戏,玩最久的:" + "、".join(parts) + "。"

    def find_game(self, name: str) -> dict | None:
        want = _norm(name)
        if not want:
            return None
        games = self.owned()
        exact = [g for g in games if _norm(g.get("name", "")) == want]
        if exact:
            return exact[0]
        hits = [g for g in games if want in _norm(g.get("name", ""))]
        return max(hits, key=lambda g: g.get("playtime_forever", 0)) if hits else None

    def game_time(self, name: str) -> str | None:
        game = self.find_game(name)
        if game is None:
            return None
        text = f"{game['name']} 你一共玩了 {hours(game.get('playtime_forever', 0))}"
        if game.get("playtime_2weeks"):
            text += f",最近两周 {hours(game['playtime_2weeks'])}"
        return text + "。"

    # ---------- 好友 ----------
    def friends(self) -> str:
        data = self._call("ISteamUser/GetFriendList/v1/", steamid=self.my_id(), relationship="friend")
        ids = [f["steamid"] for f in data.get("friendslist", {}).get("friends", [])]
        if not ids:
            return "读不到好友列表(个人资料的“好友列表”要设成公开)。"
        people = []
        for start in range(0, min(len(ids), 300), 100):
            chunk = ",".join(ids[start:start + 100])
            people += self._call("ISteamUser/GetPlayerSummaries/v2/", steamids=chunk).get("response", {}).get("players", [])
        playing = [p for p in people if p.get("gameextrainfo")]
        online = [p for p in people if p.get("personastate", 0) and not p.get("gameextrainfo")]
        if not playing and not online:
            return f"{len(ids)} 个 Steam 好友现在都不在线。"
        parts = []
        if playing:
            parts.append("在玩游戏:" + "、".join(f"{p.get('personaname', '?')}({p['gameextrainfo']})" for p in playing[:10]))
        if online:
            parts.append("在线:" + "、".join(f"{p.get('personaname', '?')}"
                                            + (f"({STATES.get(p.get('personastate'), '')})" if p.get('personastate') not in (1,) else "")
                                            for p in online[:12]))
        return ";".join(parts) + "。"
