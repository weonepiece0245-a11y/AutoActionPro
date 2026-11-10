"""专注模式 / 番茄钟:说“专注 25 分钟”就开始计时,时间到叫你休息 5 分钟(不花钱)。

专注的时候,星期五自己主动要说的事(下雨、报告、生日、账单、新邮件……)先压着,时间到再一起说;
你自己设的提醒和日历活动照常提醒。每天专注了多久记在 friday_focus.json。
"""

from __future__ import annotations

import datetime
import json
import os
import re
import time
from pathlib import Path
from typing import Callable

DEFAULT_MINUTES = 25
BREAK_MINUTES = 5
MAX_MINUTES = 180
CN_DIGITS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def cn_number(text: str) -> int | None:
    """“25”“二十五”“四十”“十五”“半小时”“一个小时”“1.5小时” -> 分钟(认不出来返回 None)。"""
    text = str(text or "").strip().replace("个", "")
    if not text:
        return None
    hours = text.endswith(("小时", "钟头"))
    text = re.sub(r"(小时|钟头|分钟|分)$", "", text)
    if text in ("半", "半个"):
        return 30 if hours else None
    if text.endswith("半") and hours:
        whole = cn_number(text[:-1])
        return whole * 60 + 30 if whole is not None else None
    try:
        value = float(text)
    except ValueError:
        value = None
        if text and all(c in CN_DIGITS or c == "十" for c in text):
            if "十" in text:
                tens, _, ones = text.partition("十")
                value = (CN_DIGITS.get(tens, 1) if tens else 1) * 10 + (CN_DIGITS.get(ones, 0) if ones else 0)
            elif len(text) == 1:
                value = CN_DIGITS[text]
    if value is None:
        return None
    return int(round(value * 60)) if hours else int(value)


def minutes_text(minutes: float) -> str:
    minutes = int(round(minutes))
    if minutes < 60:
        return f"{minutes} 分钟"
    return f"{minutes // 60} 小时" + (f" {minutes % 60} 分钟" if minutes % 60 else "")


class Focus:
    def __init__(self, path: str | os.PathLike, clock: Callable[[], float] = time.time,
                 today: Callable[[], datetime.date] = datetime.date.today) -> None:
        self.path = Path(path)
        self.clock = clock
        self.today = today
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        data = data if isinstance(data, dict) else {}
        self.days: dict[str, dict] = {k: v for k, v in (data.get("days") or {}).items() if isinstance(v, dict)}
        current = data.get("current")
        self.current: dict | None = current if isinstance(current, dict) and current.get("until") else None
        self.break_until: float | None = data.get("break_until")
        self.held: list[str] = []
        self.on_change: Callable[[dict], None] = lambda state: None  # 开始/结束时通知窗口(倒计时)

    def _save(self) -> None:
        try:
            temp = self.path.with_name(self.path.name + ".tmp")
            temp.write_text(json.dumps({"days": self.days, "current": self.current, "break_until": self.break_until},
                                       ensure_ascii=False), encoding="utf-8")
            os.replace(temp, self.path)
        except OSError:
            pass

    def _changed(self) -> None:
        self._save()
        try:
            self.on_change(self.state())
        except Exception:
            pass

    # ---------- 状态 ----------
    def active(self) -> bool:
        return self.current is not None

    def remaining(self) -> float:
        return max(0.0, self.current["until"] - self.clock()) if self.current else 0.0

    def state(self) -> dict:
        return {"until": self.current["until"] if self.current else 0, "task": (self.current or {}).get("task", ""),
                "minutes": (self.current or {}).get("minutes", 0), "break_until": self.break_until or 0}

    def _record(self, minutes: float, finished: bool) -> None:
        if minutes < 1:
            return
        day = self.days.setdefault(self.today().isoformat(), {"minutes": 0, "count": 0})
        day["minutes"] = round(day.get("minutes", 0) + minutes, 1)
        if finished:
            day["count"] = day.get("count", 0) + 1
        cutoff = (self.today() - datetime.timedelta(days=400)).isoformat()
        self.days = {k: v for k, v in self.days.items() if k >= cutoff}

    def _held_text(self) -> str:
        if not self.held:
            return ""
        text = f"刚才专注时有 {len(self.held)} 件事先替你压着:" + ";".join(self.held) + "。"
        self.held = []
        return text

    # ---------- 开始 / 结束 ----------
    def start(self, minutes: int | None = None, task: str = "") -> str:
        minutes = int(minutes or DEFAULT_MINUTES)
        if not 1 <= minutes <= MAX_MINUTES:
            raise ValueError(f"专注时间要在 1 到 {MAX_MINUTES} 分钟之间")
        if self.current:
            left = self.remaining() / 60
            return f"已经在专注了,还剩 {minutes_text(max(1, left))}。要停下来就说“停止专注”。"
        now = self.clock()
        task = " ".join(str(task or "").split())[:40]
        self.current = {"started": now, "until": now + minutes * 60, "minutes": minutes, "task": task}
        self.break_until = None
        self._changed()
        what = f"“{task}”" if task else ""
        return (f"开始专注{what} {minutes_text(minutes)},到 {datetime.datetime.fromtimestamp(now + minutes * 60):%H:%M}。"
                "这段时间我不主动打扰你,时间到叫你休息。")

    def stop(self) -> str:
        if not self.current:
            self.break_until = None
            self._changed()
            return "现在没有在专注。"
        done = (self.clock() - self.current["started"]) / 60
        self._record(done, finished=False)
        self.current = None
        self.break_until = None
        self._changed()
        if done < 1:
            return "好,这次专注取消了。" + self._held_text()
        return f"好,专注结束,这次专注了 {minutes_text(done)}。" + self._held_text()

    def hold(self, text: str, short: str | None = None) -> None:
        self.held.append(short or text)

    def tick(self, name: str = "老板") -> list[str]:
        """时间到了就返回要说的话(专注结束叫你休息;休息完叫你回来)。"""
        now = self.clock()
        said = []
        if self.current and now >= self.current["until"]:
            minutes = self.current["minutes"]
            self._record(minutes, finished=True)
            self.current = None
            self.break_until = now + BREAK_MINUTES * 60
            count = self.days.get(self.today().isoformat(), {}).get("count", 0)
            said.append(f"{name},专注 {minutes_text(minutes)}结束了,今天第 {count} 个番茄。"
                        f"休息 {BREAK_MINUTES} 分钟吧,站起来走走、喝口水。" + self._held_text())
            self._changed()
        elif self.break_until and not self.current and now >= self.break_until:
            self.break_until = None
            said.append(f"{name},休息时间到了。要继续就说“专注”。")
            self._changed()
        return said

    # ---------- 统计 ----------
    def report(self, period: str = "today") -> str:
        today = self.today()
        if period == "week":
            first = today - datetime.timedelta(days=today.weekday())
            days = [(first + datetime.timedelta(days=i)).isoformat() for i in range((today - first).days + 1)]
            label = "这周"
        else:
            days = [today.isoformat()]
            label = "今天"
        minutes = sum(self.days.get(d, {}).get("minutes", 0) for d in days)
        count = sum(self.days.get(d, {}).get("count", 0) for d in days)
        now = ""
        if self.current:
            now = f"现在正在专注,还剩 {minutes_text(max(1, self.remaining() / 60))}。"
        if not minutes:
            return now + f"{label}还没有专注记录。说“专注 25 分钟”就能开始。"
        return now + f"{label}专注了 {minutes_text(minutes)},完成 {count} 个番茄。"

    def status(self) -> str:
        if self.current:
            task = f"“{self.current['task']}”" if self.current.get("task") else ""
            return f"正在专注{task},还剩 {minutes_text(max(1, self.remaining() / 60))}。"
        if self.break_until and self.break_until > self.clock():
            return f"正在休息,还有 {minutes_text(max(1, (self.break_until - self.clock()) / 60))}。"
        return "现在没有在专注。说“专注 25 分钟”就能开始。"
