"""Dota 2 战绩:刚才那局怎么样、最近几局输赢、哪个英雄胜率最高、现在什么段位(OpenDota 免费数据,不花钱)。

用你在齿轮设定里填好的 Steam 账号。要在 Dota 2 里打开“公开比赛数据”(设置 → 选项 → 社交 → Expose Public
Match Data),OpenDota 才看得到你的比赛。打完一局,星期五会在几分钟后主动告诉你战绩。
"""

from __future__ import annotations

import datetime
import json
import time
import urllib.error
import urllib.request
from typing import Callable

API = "https://api.opendota.com/api"
STEAM64_BASE = 76561197960265728
RANKS = {1: "先锋", 2: "卫士", 3: "中军", 4: "统帅", 5: "传奇", 6: "万古流芳", 7: "超凡入圣", 8: "冠绝一世"}
MODES = {1: "全英雄选择", 2: "队长模式", 3: "随机征召", 4: "单一征召", 5: "全部随机", 16: "队长征召", 18: "技能征召",
         22: "全英雄选择", 23: "加速模式"}
HERO_CACHE_SECONDS = 7 * 86400


def fetch_json(url: str, method: str = "GET"):
    request = urllib.request.Request(url, method=method, headers={"User-Agent": "Friday"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310
            raw = response.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise ValueError("OpenDota 说问得太频繁了,过一分钟再问") from None
        raise ValueError(f"OpenDota 暂时连不上(HTTP {e.code})") from None
    except (urllib.error.URLError, OSError, ValueError):
        raise ValueError("OpenDota 暂时连不上,等一下再试") from None


def account_id(steam_id: str) -> int:
    return int(steam_id) - STEAM64_BASE


def rank_text(rank_tier) -> str | None:
    if not rank_tier:
        return None
    medal, stars = divmod(int(rank_tier), 10)
    name = RANKS.get(medal)
    if not name:
        return None
    return name if medal == 8 or not stars else f"{name} {stars} 星"


def minutes(seconds: int) -> str:
    return f"{int(seconds) // 60} 分钟"


def won(match: dict) -> bool:
    radiant = match.get("player_slot", 0) < 128
    return bool(match.get("radiant_win")) == radiant


class Dota:
    def __init__(self, steam_id: Callable[[], str], fetch=fetch_json, clock: Callable[[], float] = time.time) -> None:
        self.steam_id = steam_id  # () -> 17 位 Steam ID
        self.fetch = fetch
        self.clock = clock
        self._heroes: tuple[float, dict[int, str]] | None = None
        self.last_reported: int = 0  # 上次主动报过的那一局

    def _id(self) -> int:
        try:
            return account_id(self.steam_id())
        except (TypeError, ValueError):
            raise ValueError("要先在齿轮设定 → 连接的应用 里连好 Steam,才知道你的 Dota 账号") from None

    def heroes(self) -> dict[int, str]:
        if self._heroes and self.clock() - self._heroes[0] < HERO_CACHE_SECONDS:
            return self._heroes[1]
        data = self.fetch(f"{API}/heroes")
        table = {int(h["id"]): h.get("localized_name", str(h["id"])) for h in data if isinstance(h, dict) and "id" in h}
        self._heroes = (self.clock(), table)
        return table

    def recent(self) -> list[dict]:
        data = self.fetch(f"{API}/players/{self._id()}/recentMatches")
        if not isinstance(data, list):
            return []
        return data

    def _private_hint(self) -> str:
        return ("OpenDota 看不到你的比赛。到 Dota 2 的 设置 → 选项 → 社交,打开“公开比赛数据”"
                "(Expose Public Match Data),再打一局就看得到。")

    def describe_match(self, match: dict) -> str:
        hero = self.heroes().get(int(match.get("hero_id", 0)), "未知英雄")
        result = "赢了" if won(match) else "输了"
        k, d, a = match.get("kills", 0), match.get("deaths", 0), match.get("assists", 0)
        when = datetime.datetime.fromtimestamp(match.get("start_time", 0) + match.get("duration", 0))
        ago = self.clock() - when.timestamp()
        ago_text = f"{int(ago // 60)} 分钟前" if ago < 3600 else (f"{int(ago // 3600)} 小时前" if ago < 86400 else f"{when:%m/%d %H:%M}")
        parts = [f"{ago_text}那局用 {hero}{result},{minutes(match.get('duration', 0))},KDA {k}/{d}/{a}"]
        if match.get("gold_per_min"):
            parts.append(f"GPM {match['gold_per_min']}、XPM {match.get('xp_per_min', 0)}、正补 {match.get('last_hits', 0)}")
        if match.get("hero_damage"):
            parts.append(f"英雄伤害 {match['hero_damage']:,}")
        text = ",".join(parts) + "。"
        if d == 0 and k + a >= 10:
            text += "一次都没死,厉害!"
        elif d >= 12:
            text += "死得有点多,下局稳一点。"
        return text

    def last_match(self) -> str:
        matches = self.recent()
        if not matches:
            return self._private_hint()
        return self.describe_match(matches[0])

    def summary(self, count: int = 10) -> str:
        matches = self.recent()[:count]
        if not matches:
            return self._private_hint()
        wins = sum(won(m) for m in matches)
        heroes = self.heroes()
        streak_kind = won(matches[0])
        streak = 0
        for m in matches:
            if won(m) != streak_kind:
                break
            streak += 1
        kda = [(m.get("kills", 0), m.get("deaths", 0), m.get("assists", 0)) for m in matches]
        avg = tuple(round(sum(x[i] for x in kda) / len(kda), 1) for i in range(3))
        used: dict[str, int] = {}
        for m in matches:
            name = heroes.get(int(m.get("hero_id", 0)), "?")
            used[name] = used.get(name, 0) + 1
        top = "、".join(f"{n}({c} 局)" for n, c in sorted(used.items(), key=lambda kv: -kv[1])[:3])
        text = (f"最近 {len(matches)} 局赢 {wins} 输 {len(matches) - wins},平均 KDA {avg[0]:g}/{avg[1]:g}/{avg[2]:g},"
                f"常用 {top}。")
        if streak >= 3:
            text += f"现在{'连胜' if streak_kind else '连败'} {streak} 局。"
        return text

    def best_heroes(self, min_games: int = 10) -> str:
        data = self.fetch(f"{API}/players/{self._id()}/heroes")
        rows = [h for h in data if isinstance(h, dict) and h.get("games", 0) >= min_games] if isinstance(data, list) else []
        if not rows:
            return self._private_hint() if not data else f"还没有哪个英雄打满 {min_games} 局。"
        heroes = self.heroes()
        best = sorted(rows, key=lambda h: -h["win"] / h["games"])[:5]
        most = sorted(rows, key=lambda h: -h["games"])[:3]
        b = "、".join(f"{heroes.get(int(h['hero_id']), '?')} {h['win'] / h['games']:.0%}({h['games']} 局)" for h in best)
        m = "、".join(f"{heroes.get(int(h['hero_id']), '?')} {h['games']} 局" for h in most)
        return f"胜率最高(打满 {min_games} 局的):{b}。玩最多的:{m}。"

    def profile(self) -> str:
        data = self.fetch(f"{API}/players/{self._id()}")
        if not isinstance(data, dict) or not data.get("profile"):
            return self._private_hint()
        rank = rank_text(data.get("rank_tier"))
        wl = self.fetch(f"{API}/players/{self._id()}/wl")
        total = (wl.get("win", 0) + wl.get("lose", 0)) if isinstance(wl, dict) else 0
        text = f"{data['profile'].get('personaname', '你')}:" + (f"段位 {rank}" if rank else "还没有段位(或没公开)")
        if data.get("leaderboard_rank"):
            text += f"(天梯第 {data['leaderboard_rank']} 名)"
        if total:
            text += f",一共 {total} 局,胜率 {wl['win'] / total:.0%}"
        return text + "。"

    def refresh(self) -> None:
        """请 OpenDota 去 Steam 拿最新的比赛(打完一局后用)。"""
        try:
            self.fetch(f"{API}/players/{self._id()}/refresh", method="POST")
        except TypeError:  # 测试用的 fetch 不收 method
            self.fetch(f"{API}/players/{self._id()}/refresh")

    def new_match_report(self, name: str, since: float) -> str | None:
        """打完一局后:有新的、在 since 之后结束的比赛,而且还没报过,就返回要说的话。"""
        matches = self.recent()
        if not matches:
            return None
        match = matches[0]
        ended = match.get("start_time", 0) + match.get("duration", 0)
        if match.get("match_id") == self.last_reported or ended < since:
            return None
        self.last_reported = match.get("match_id", 0)
        return f"{name},Dota 打完啦:" + self.describe_match(match)
