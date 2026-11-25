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
    return re.sub(r"[^0-9a-z一-鿿]", "", str(name or "").lower())


# Steam 装的不是游戏的东西(运行库、工具、原声带……):开游戏、统计都不算
TOOLS = re.compile(r"steamworks|redistributable|steam linux runtime|proton|steamvr|steam audio|soundtrack|"
                   r"dedicated server|\bsdk\b|\bbenchmark\b|wallpaper engine|steam controller|"
                   r"original score|artbook|\bost\b|workshop tools|\beditor\b|\bmod tools\b", re.I)

# 中文叫法 -> Steam 上的名字(找游戏、开游戏用)
ALIASES = {
    "绝地求生": "PUBG", "吃鸡": "PUBG", "pubg": "PUBG", "刀塔": "Dota 2", "dota": "Dota 2", "dota2": "Dota 2",
    "反恐精英": "Counter-Strike 2", "cs2": "Counter-Strike 2", "csgo": "Counter-Strike 2", "cs": "Counter-Strike 2",
    "apex": "Apex Legends", "apex英雄": "Apex Legends", "艾尔登法环": "ELDEN RING", "老头环": "ELDEN RING",
    "黑神话": "Black Myth: Wukong", "黑神话悟空": "Black Myth: Wukong", "悟空": "Black Myth: Wukong",
    "永劫无间": "NARAKA: BLADEPOINT", "双人成行": "It Takes Two", "双影奇境": "Split Fiction",
    "怪物猎人": "Monster Hunter", "怪猎": "Monster Hunter", "赛博朋克": "Cyberpunk 2077", "2077": "Cyberpunk 2077",
    "幻兽帕鲁": "Palworld", "帕鲁": "Palworld", "雾锁王国": "Enshrouded", "致命公司": "Lethal Company",
    "鹅鸭杀": "Goose Goose Duck", "彩虹六号": "Rainbow Six", "r6": "Rainbow Six", "命运2": "Destiny 2",
    "使命召唤": "Call of Duty", "cod": "Call of Duty", "战地": "Battlefield", "荒野大镖客": "Red Dead Redemption",
    "大表哥": "Red Dead Redemption", "侠盗猎车": "Grand Theft Auto", "gta": "Grand Theft Auto", "gta5": "Grand Theft Auto V",
    "我的世界": "Minecraft", "泰拉瑞亚": "Terraria", "饥荒": "Don't Starve", "星露谷": "Stardew Valley",
    "糖豆人": "Fall Guys", "火箭联盟": "Rocket League", "博德之门": "Baldur's Gate 3", "博德之门3": "Baldur's Gate 3",
    "只狼": "Sekiro", "黑魂": "DARK SOULS", "鬼泣": "Devil May Cry", "生化危机": "Resident Evil",
    "原子之心": "Atomic Heart", "霍格沃茨": "Hogwarts Legacy", "星空": "Starfield", "流放之路": "Path of Exile",
    "暗黑破坏神": "Diablo", "暗黑": "Diablo", "守望先锋": "Overwatch", "最终幻想": "FINAL FANTASY",
    "无畏契约": "VALORANT", "瓦洛兰特": "VALORANT", "rocksmith": "Rocksmith",
}


def is_tool(name: str) -> bool:
    """Steam 上的工具、运行库、原声带(不是游戏)。"""
    import friday_games

    return bool(TOOLS.search(str(name or "")) or friday_games.NOT_GAME_HINTS.search(str(name or "")))


def alias(query: str) -> str:
    q = _norm(query)
    for key, value in sorted(ALIASES.items(), key=lambda kv: -len(kv[0])):
        if _norm(key) == q:
            return value
    return query


def score(name: str, query: str) -> int:
    """名字和要找的有多像(越大越像;0 = 不像)。不分大小写、不管标点和 ™®。"""
    n, q = _norm(name), _norm(alias(query))
    if not n or not q:
        return 0
    if n == q:
        return 100
    words = [w for w in re.split(r"[^0-9a-z一-鿿]+", str(alias(query)).lower()) if w]
    if n.startswith(q):
        return 80 - min(20, len(n) - len(q))  # 名字越短越像(“Dota 2”比“Dota 2 Workshop Tools”像)
    if q in n:
        return 60 - min(20, len(n) - len(q))
    if len(words) > 1 and all(w in n for w in words):
        return 40
    return 0


def best(names: list[str], query: str) -> int | None:
    """名字列表里最像 query 的那个的位置;都不像 None。"""
    scored = [(score(name, query), -len(name), i) for i, name in enumerate(names)]
    top = max(scored, default=(0, 0, None))
    return top[2] if top[0] > 0 else None


def suggestions(names: list[str], query: str, count: int = 3) -> list[str]:
    import difflib

    lowered = {n.lower(): n for n in names}
    close = difflib.get_close_matches(str(alias(query)).lower(), list(lowered), n=count, cutoff=0.6)
    return [lowered[c] for c in close]


class Steam:
    def __init__(self, fetch: Callable[[str], dict] = fetch_json, clock: Callable[[], float] = time.time) -> None:
        self.fetch = fetch
        self.clock = clock
        self.steam_id: str | None = None
        self._owned: tuple[float, list[dict]] | None = None
        self.persona = ""  # 你的 Steam 名字(设定里显示连上了谁)

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

    def profile(self) -> str:
        """你的 Steam 名字(顺便确认钥匙和个人资料都对)。"""
        players = self._call("ISteamUser/GetPlayerSummaries/v2/", steamids=self.my_id()).get("response", {}).get("players", [])
        if not players:
            raise ValueError("找不到这个 Steam 个人资料")
        self.persona = str(players[0].get("personaname") or "")
        return self.persona

    # ---------- 游戏 ----------
    def recent(self) -> str:
        games = self._call("IPlayerService/GetRecentlyPlayedGames/v1/", steamid=self.my_id()).get("response", {}).get("games", [])
        games = [g for g in games if not is_tool(g.get("name", ""))]
        if not games:
            return "最近两周 Steam 上没有玩游戏的记录(也可能是个人资料的“游戏详情”没设成公开)。"
        games.sort(key=lambda g: -g.get("playtime_2weeks", 0))
        total = sum(g.get("playtime_2weeks", 0) for g in games)
        lines = [f"• {g.get('name', '?')}:{hours(g.get('playtime_2weeks', 0))}" for g in games[:5]]
        more = f"\n(还有 {len(games) - 5} 个玩得比较少)" if len(games) > 5 else ""
        return f"最近两周在 Steam 上玩了 {hours(total)}:\n" + "\n".join(lines) + more

    def owned(self) -> list[dict]:
        if self._owned and self.clock() - self._owned[0] < CACHE_SECONDS:
            return self._owned[1]
        games = self._call("IPlayerService/GetOwnedGames/v1/", steamid=self.my_id(), include_appinfo=1,
                           include_played_free_games=1).get("response", {}).get("games", [])
        games = [g for g in games if not is_tool(g.get("name", ""))]
        self._owned = (self.clock(), games)
        return games

    def top(self, count: int = 5) -> str:
        games = sorted(self.owned(), key=lambda g: -g.get("playtime_forever", 0))
        if not games:
            return "读不到你的游戏库(个人资料的“游戏详情”要设成公开)。"
        played = [g for g in games if g.get("playtime_forever")]
        lines = [f"• {g.get('name', '?')}:{hours(g.get('playtime_forever', 0))}" for g in played[:count]]
        total = sum(g.get("playtime_forever", 0) for g in played)
        return (f"你的 Steam 有 {len(games)} 个游戏(玩过 {len(played)} 个,一共 {hours(total)}),玩最久的:\n"
                + "\n".join(lines))

    def find_game(self, name: str) -> dict | None:
        games = self.owned()
        index = best([g.get("name", "") for g in games], name)
        return games[index] if index is not None else None

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
        lines = [f"{len(playing) + len(online)} 个好友在线:"]
        by_game: dict[str, list[str]] = {}
        for p in playing:  # 同一个游戏的放一起
            by_game.setdefault(p["gameextrainfo"], []).append(p.get("personaname", "?"))
        for game, names in sorted(by_game.items(), key=lambda kv: -len(kv[1]))[:6]:
            lines.append(f"• 在玩 {game}:{'、'.join(names[:5])}" + (f" 等 {len(names)} 人" if len(names) > 5 else ""))
        if online:
            names = [p.get("personaname", "?") for p in online]
            lines.append("• 在线没在玩:" + "、".join(names[:8]) + (f" 等 {len(names)} 人" if len(names) > 8 else ""))
        return "\n".join(lines)
