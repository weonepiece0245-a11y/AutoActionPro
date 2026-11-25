"""电脑使用时间统计:每 30 秒看一眼最前面的程序,你在用(或在看视频、打游戏)就记下来。

只记在你自己的电脑上(friday_screentime.json,留 60 天),不问 Claude,不花钱。
"""

from __future__ import annotations

import datetime
import json
import os
from pathlib import Path
from typing import Callable

KEEP_DAYS = 60
MAX_GAP = 90  # 两次记录隔太久(电脑睡眠过)就不算
ACTIVE_IDLE = 5 * 60  # 5 分钟没碰键盘鼠标(也没在看视频)就不算在用

NAMES = {
    "chrome.exe": "Chrome", "msedge.exe": "Edge(含星期五窗口)", "firefox.exe": "Firefox",
    "discord.exe": "Discord", "explorer.exe": "桌面/文件管理器", "code.exe": "VS Code", "winword.exe": "Word",
    "excel.exe": "Excel", "powerpnt.exe": "PowerPoint", "spotify.exe": "Spotify", "steam.exe": "Steam",
    "steamwebhelper.exe": "Steam", "weixin.exe": "微信", "wechat.exe": "微信", "whatsapp.exe": "WhatsApp",
    "telegram.exe": "Telegram", "vlc.exe": "VLC", "notepad.exe": "记事本", "windowsterminal.exe": "终端",
    "obs64.exe": "OBS", "epicgameslauncher.exe": "Epic", "pythonw.exe": "星期五", "python.exe": "Python",
}


def app_name(exe: str, game: str | None = None) -> str:
    if game:
        return game
    exe = (exe or "").lower()
    if not exe:
        return "其他"
    return NAMES.get(exe) or os.path.splitext(exe)[0]


def hours_minutes(seconds: float) -> str:
    minutes = int(seconds // 60)
    hours, rest = divmod(minutes, 60)
    if hours and rest:
        return f"{hours} 小时 {rest} 分钟"
    return f"{hours} 小时" if hours else f"{minutes} 分钟"


class ScreenTime:
    def __init__(self, path: str | os.PathLike, clock: Callable[[], datetime.datetime] = datetime.datetime.now) -> None:
        self.path = Path(path)
        self.clock = clock
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        days = data.get("days") if isinstance(data, dict) else None
        self.days: dict[str, dict] = days if isinstance(days, dict) else {}
        self.last: datetime.datetime | None = None
        self.unsaved = 0

    def save(self) -> None:
        for old in sorted(self.days)[:-KEEP_DAYS]:
            del self.days[old]
        temp = self.path.with_name(self.path.name + ".tmp")
        try:
            temp.write_text(json.dumps({"days": self.days}, ensure_ascii=False), encoding="utf-8")
            os.replace(temp, self.path)
        except OSError:
            pass
        self.unsaved = 0

    def record(self, snapshot: dict | None) -> None:
        """主动关心每看一次就调用一次(snapshot 是 friday_care.look 的结果)。"""
        now = self.clock()
        gap = (now - self.last).total_seconds() if self.last else 0
        self.last = now
        if snapshot is None or not 0 < gap <= MAX_GAP:
            return
        if snapshot.get("present_idle", 0) >= ACTIVE_IDLE:
            return  # 人不在
        name = app_name(snapshot.get("app", ""), snapshot.get("game"))
        day = self.days.setdefault(now.strftime("%Y-%m-%d"), {"apps": {}, "games": {}})
        day["apps"][name] = day["apps"].get(name, 0) + gap
        if snapshot.get("game"):
            day["games"][name] = day["games"].get(name, 0) + gap
        self.unsaved += 1
        if self.unsaved >= 10:  # 大约每 5 分钟存一次
            self.save()

    def totals(self, first: datetime.date, last: datetime.date) -> tuple[dict, dict]:
        apps: dict[str, float] = {}
        games: dict[str, float] = {}
        for day, data in self.days.items():
            if first.isoformat() <= day <= last.isoformat():
                for name, seconds in data.get("apps", {}).items():
                    apps[name] = apps.get(name, 0) + seconds
                for name, seconds in data.get("games", {}).items():
                    games[name] = games.get(name, 0) + seconds
        return apps, games

    def report(self, period: str = "today") -> str:
        today = self.clock().date()
        if period == "yesterday":
            first = last = today - datetime.timedelta(days=1)
            label = "昨天"
        elif period == "week":
            first, last, label = today - datetime.timedelta(days=today.weekday()), today, "这周"
        else:
            first = last = today
            label = "今天"
        apps, games = self.totals(first, last)
        if not apps:
            return f"{label}还没有使用记录(电脑使用时间是从这次更新之后才开始记的)"
        total = sum(apps.values())
        top = sorted(apps.items(), key=lambda kv: -kv[1])[:6]
        text = f"{label}用电脑大约 {hours_minutes(total)}。用得最多的:" + "、".join(
            f"{name} {hours_minutes(seconds)}" for name, seconds in top) + "。"
        if games:
            game_total = sum(games.values())
            text += f"其中打游戏 {hours_minutes(game_total)}(" + "、".join(
                f"{name} {hours_minutes(seconds)}" for name, seconds in sorted(games.items(), key=lambda kv: -kv[1])) + ")。"
        if period == "week" and len(self.days) and (last - first).days >= 1:
            text += f"平均每天 {hours_minutes(total / ((last - first).days + 1))}。"
        return text

    def week_line(self, first: datetime.date, last: datetime.date) -> str:
        """给每周报告用的一句话。"""
        apps, games = self.totals(first, last)
        if not apps:
            return ""
        text = f"这周用电脑大约 {hours_minutes(sum(apps.values()))}"
        if games:
            top = max(games.items(), key=lambda kv: kv[1])
            text += f",打游戏 {hours_minutes(sum(games.values()))}(最多的是 {top[0]} {hours_minutes(top[1])})"
        return text + "。"
