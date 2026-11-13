"""每周游戏和电脑总结:星期天晚上跟花费报告一起说(也推到手机,不花钱)。

这周玩了多久游戏(和上周比)、每个游戏多久、打了几局、最长的一局、电脑最热几度、
哪个后台程序常常在你打游戏时很忙;连了 Dota 2 / Apex 的话再加一句战绩和段位。
"""

from __future__ import annotations

import datetime
from typing import Callable


def _hours(seconds: float) -> str:
    import friday_screentime

    return friday_screentime.hours_minutes(seconds)


def _day_range(first: datetime.date, last: datetime.date) -> tuple[float, float]:
    start = datetime.datetime.combine(first, datetime.time()).timestamp()
    end = datetime.datetime.combine(last + datetime.timedelta(days=1), datetime.time()).timestamp()
    return start, end


def game_section(week: tuple[datetime.date, datetime.date], screentime=None, report=None, dota=None,
                 apex_line: Callable[[], str] | None = None) -> str:
    first, last = week
    parts = []
    if screentime is not None:
        _, games = screentime.totals(first, last)
        _, before = screentime.totals(first - datetime.timedelta(days=7), last - datetime.timedelta(days=7))
        if games:
            total, prev = sum(games.values()), sum(before.values())
            top = "、".join(f"{name} {_hours(s)}" for name, s in sorted(games.items(), key=lambda kv: -kv[1])[:3])
            compare = ""
            if prev:
                diff = total - prev
                compare = f",比上周{'多' if diff > 0 else '少'} {_hours(abs(diff))}" if abs(diff) >= 600 else ",和上周差不多"
            parts.append(f"游戏:{top}{compare}。")
    if report is not None:
        sessions = report.sessions(*_day_range(first, last))
        if sessions:
            longest = max(sessions, key=lambda e: e.get("seconds", 0))
            text = f"打了 {len(sessions)} 局(5 分钟以上的),最长的一局是 {longest['game']} {_hours(longest['seconds'])}"
            cpu = max((e.get("cpu_temp") or 0 for e in sessions), default=0)
            gpu = max((e.get("gpu_temp") or 0 for e in sessions), default=0)
            hot = [f"CPU {cpu:.0f}°C" for _ in [0] if cpu] + [f"显卡 {gpu:.0f}°C" for _ in [0] if gpu]
            if hot:
                text += ";打游戏时最热:" + "、".join(hot)
            counts: dict[str, int] = {}
            for e in sessions:
                for name in e.get("busy") or []:
                    counts[name] = counts.get(name, 0) + 1
            busy = [name for name, n in sorted(counts.items(), key=lambda kv: -kv[1]) if n >= max(2, len(sessions) // 2)]
            if busy:
                import friday_gamereport

                text += ";常常在你打游戏时很忙的后台程序:" + "、".join(friday_gamereport.FRIENDLY.get(n, n) for n in busy[:2])
            parts.append(text + "。")
    if dota is not None:
        try:
            import friday_dota

            start, end = _day_range(first, last)
            matches = [m for m in dota.recent() if start <= m.get("start_time", 0) < end]
            if matches:
                wins = sum(friday_dota.won(m) for m in matches)
                parts.append(f"Dota 2 这周 {len(matches)} 局,赢 {wins} 输 {len(matches) - wins}。")
        except Exception:
            pass
    if apex_line is not None:
        try:
            line = apex_line()
            if line:
                parts.append(f"Apex:{line}")
        except Exception:
            pass
    return ("【游戏和电脑】" + "".join(parts)) if parts else ""
