"""练琴记录和目标:你开吉他软件(BIAS FX、Neural DSP、Guitar Pro、Rocksmith、调音器……)练琴时自动记时间,
每天、每周统计、连续练了几天;设了目标(例如每天 30 分钟)的话,晚上还没练够会提醒一次。

- 怎么算在练:吉他软件开着,而且它在最前面、或你 10 分钟内碰过电脑(弹琴时手不在键盘上也算);
  软件开着人走开很久就不算;
- 不插电练的:跟星期五说“我练了 30 分钟吉他”手动记;
- “节拍器 90”窗口直接打拍子(“节拍器 120 三拍”“节拍器停”);
- 只记在你的电脑(friday_practice.json),不花钱。
"""

from __future__ import annotations

import datetime
import json
import os
import re
import threading
from pathlib import Path
from typing import Callable

GUITAR_APPS = re.compile(
    r"guitar|bias ?fx|biasfx|neural|archetype|amplitube|tonex|rocksmith|yousician|tuner|helix|positive ?grid|"
    r"fender ?tone|boss ?tone|tone ?studio|songsterr|ultimate ?guitar|gp7|gp8|guitarpro|mooer|nux|headrush|"
    r"spark ?amp|kemper|line ?6|pod ?go|ampli|amp ?sim|jamtrack|moises|transcribe", re.I)
PRESENT_IDLE = 10 * 60  # 软件不在最前面时:这么久没碰电脑就不算在练
SESSION_GAP = 5 * 60  # 隔这么久没在练,算下一次
MIN_SESSION = 3 * 60  # 太短的不算一次
GOALS = (0, 10, 15, 20, 30, 45, 60, 90, 120)
REMIND_TIMES = ("off", "19:00", "20:00", "21:00", "22:00", "23:00")
KEEP_DAYS = 400
CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "半": 0}

LOG = re.compile(r"^(星期五)?[,，\s]*(我)?(刚才|刚刚|今天)?(练|弹)了?\s*(?P<n>\d{1,3}|[一二两三四五六七八九十]{1,3}|半)\s*(个)?"
                 r"(?P<u>分钟|分|小时|钟头)(的)?(吉他|琴|guitar)?[。!！]?$", re.I)
LOG2 = re.compile(r"^(星期五)?[,，\s]*(我)?(刚才|刚刚|今天)?(练|弹)了?(吉他|琴|guitar)\s*(?P<n>\d{1,3}|[一二两三四五六七八九十]{1,3}|半)\s*(个)?"
                  r"(?P<u>分钟|分|小时|钟头)[。!！]?$", re.I)
STATS = re.compile(r"^(星期五)?[,，\s]*(练琴|练吉他)(的)?(记录|统计|时间|情况|进度)?[?？]?$|^(我)?(今天|这周|这个星期|本周)(练了多久|练琴了吗|练了多少)(琴|吉他)?[?？]?$")
METRO = re.compile(r"^(星期五)?[,，\s]*(开|打开|来个?)?(节拍器|metronome)\s*(?P<bpm>\d{2,3})?\s*(bpm)?\s*"
                   r"(?P<beats>[2-7二三四五六七]\s*(拍|/4|/8))?[。!！]?$", re.I)
METRO_STOP = re.compile(r"^(星期五)?[,，\s]*(节拍器|metronome)\s*(停|关|stop|off)(掉|了)?[。!！]?$|^(停|关)(掉)?(节拍器|metronome)[。!！]?$", re.I)


def goal_minutes() -> int:
    try:
        value = int(os.environ.get("FRIDAY_PRACTICE_GOAL", "0"))
    except ValueError:
        return 0
    return value if value in GOALS else 0


def remind_time() -> str:
    value = os.environ.get("FRIDAY_PRACTICE_REMIND", "21:00").strip()
    return value if value in REMIND_TIMES else "21:00"


def extra_apps() -> list[str]:
    return [a.strip().lower() for a in os.environ.get("FRIDAY_PRACTICE_APPS", "").split(",") if a.strip()]


def is_guitar_app(exe: str) -> bool:
    name = os.path.splitext(os.path.basename(str(exe or "")))[0].lower()
    if not name:
        return False
    return bool(GUITAR_APPS.search(name)) or any(a.replace(".exe", "") in name for a in extra_apps())


def _num(text: str) -> float:
    if text.isdigit():
        return float(text)
    if text == "半":
        return 0.5
    if len(text) == 2 and text[0] == "十":
        return 10 + CN_NUM.get(text[1], 0)
    if len(text) >= 2 and text[1] == "十":
        return CN_NUM.get(text[0], 1) * 10 + (CN_NUM.get(text[2], 0) if len(text) > 2 else 0)
    return float(CN_NUM.get(text, 0))


class Practice:
    def __init__(self, path: str | os.PathLike,
                 clock: Callable[[], datetime.datetime] = datetime.datetime.now) -> None:
        self.path = Path(path)
        self.clock = clock
        self.lock = threading.Lock()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        self.days: dict[str, dict] = data.get("days") if isinstance(data.get("days"), dict) else {}
        self.reminded = str(data.get("reminded", ""))
        self.session: dict | None = None  # 正在练的这一次 {start, last, seconds, app}

    def _save(self) -> None:
        cutoff = (self.clock().date() - datetime.timedelta(days=KEEP_DAYS)).isoformat()
        self.days = {k: v for k, v in self.days.items() if k >= cutoff}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"days": self.days, "reminded": self.reminded}, ensure_ascii=False, indent=1),
                           encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            pass

    def _add(self, day: str, seconds: float, app: str) -> None:
        entry = self.days.setdefault(day, {"seconds": 0, "apps": {}, "sessions": 0})
        entry["seconds"] = int(entry.get("seconds", 0) + seconds)
        entry.setdefault("apps", {})[app] = int(entry["apps"].get(app, 0) + seconds)

    # ---------- 每分钟看一次 ----------
    def tick(self, running: list[str], foreground: str, idle: float | None, step: float = 60) -> str | None:
        """running:正在跑的程序;foreground:最前面的;idle:几秒没碰电脑。练完一次返回要说的话。"""
        now = self.clock()
        apps = [p for p in running if is_guitar_app(p)]
        front = foreground if is_guitar_app(foreground) else ""
        practicing = bool(front) or (bool(apps) and idle is not None and idle < PRESENT_IDLE)
        with self.lock:
            if practicing:
                app = os.path.splitext(os.path.basename(front or apps[0]))[0]
                if self.session is None:
                    self.session = {"start": now.isoformat(timespec="seconds"), "seconds": 0, "app": app}
                self.session["last"] = now.isoformat(timespec="seconds")
                self.session["seconds"] += step
                self._add(now.date().isoformat(), step, app)
                if int(self.session["seconds"]) % 600 < step:  # 每 10 分钟存一次
                    self._save()
                return None
            if self.session is None:
                return None
            last = datetime.datetime.fromisoformat(self.session["last"])
            if (now - last).total_seconds() < SESSION_GAP:
                return None
            done, self.session = self.session, None
            if done["seconds"] >= MIN_SESSION:
                day = self.days.setdefault(last.date().isoformat(), {"seconds": 0, "apps": {}, "sessions": 0})
                day["sessions"] = day.get("sessions", 0) + 1
            self._save()
        if done["seconds"] < MIN_SESSION:
            return None
        return f"🎸 刚练了 {round(done['seconds'] / 60)} 分钟({done['app']})。" + self.today_line()

    def log(self, minutes: float, app: str = "手动记") -> str:
        minutes = max(1, min(600, round(minutes)))
        now = self.clock()
        with self.lock:
            self._add(now.date().isoformat(), minutes * 60, app)
            self.days[now.date().isoformat()]["sessions"] = self.days[now.date().isoformat()].get("sessions", 0) + 1
            self._save()
        return f"🎸 记好了:练了 {minutes} 分钟。" + self.today_line()

    # ---------- 统计 ----------
    def minutes(self, day: datetime.date) -> int:
        return round(self.days.get(day.isoformat(), {}).get("seconds", 0) / 60)

    def streak(self) -> int:
        """连续几天有练(今天还没练不算断)。达标:有目标就要到目标,没目标练 10 分钟以上。"""
        need = goal_minutes() or 10
        day = self.clock().date()
        if self.minutes(day) < need:
            day -= datetime.timedelta(days=1)
        count = 0
        while self.minutes(day) >= need:
            count += 1
            day -= datetime.timedelta(days=1)
        return count

    def today_line(self) -> str:
        today = self.minutes(self.clock().date())
        goal = goal_minutes()
        text = f"今天共 {today} 分钟"
        if goal:
            text += f"(目标 {goal},{'达标了 👍' if today >= goal else f'还差 {goal - today} 分钟'})"
        streak = self.streak()
        if streak >= 2:
            text += f",连续 {streak} 天"
        return text + "。"

    def week(self) -> tuple[int, int]:
        today = self.clock().date()
        monday = today - datetime.timedelta(days=today.weekday())
        days = [monday + datetime.timedelta(days=i) for i in range((today - monday).days + 1)]
        total = sum(self.minutes(d) for d in days)
        return total, sum(1 for d in days if self.minutes(d) > 0)

    def report(self) -> str:
        week_total, week_days = self.week()
        today = self.clock().date()
        last_week = [today - datetime.timedelta(days=today.weekday() + 7 - i) for i in range(7)]
        prev = sum(self.minutes(d) for d in last_week)
        apps: dict[str, int] = {}
        for d, entry in self.days.items():
            if d >= (today - datetime.timedelta(days=30)).isoformat():
                for app, sec in entry.get("apps", {}).items():
                    apps[app] = apps.get(app, 0) + sec
        lines = ["🎸 练琴记录:" + self.today_line(),
                 f"这周练了 {week_days} 天、共 {week_total} 分钟(上周 {prev} 分钟)。"]
        if apps:
            lines.append("最近 30 天用的:" + "、".join(f"{a} {round(s / 60)} 分钟" for a, s in
                                                     sorted(apps.items(), key=lambda kv: -kv[1])[:5]))
        if not self.days:
            lines = ["还没有练琴记录。开吉他软件练就会自动记;不插电练的话跟我说“我练了 30 分钟吉他”。"]
        return "\n".join(lines)

    def due_reminder(self) -> str | None:
        """到了提醒时间、今天还没练够:提醒一次。"""
        goal, at = goal_minutes(), remind_time()
        now = self.clock()
        today = now.date().isoformat()
        if not goal or at == "off" or self.reminded == today or now.strftime("%H:%M") < at:
            return None  # 过了半夜(00:xx 比提醒时间早)也不会提醒
        done = self.minutes(now.date())
        if done >= goal or self.session is not None:
            return None
        self.reminded = today
        self._save()
        streak = self.streak()
        keep = f"连续 {streak} 天了,别断掉。" if streak >= 2 else ""
        return f"今天练琴 {done} 分钟,目标 {goal} 分钟,还差 {goal - done} 分钟。{keep}要不要现在练一下?"

    def state(self) -> dict:
        week_total, week_days = self.week()
        return {"today": self.minutes(self.clock().date()), "goal": goal_minutes(), "remind": remind_time(),
                "streak": self.streak(), "week": week_total, "week_days": week_days, "apps": ", ".join(extra_apps()),
                "now": self.session["app"] if self.session else ""}


class Commands:
    def __init__(self, practice: Practice, metronome: Callable[[int, int], None] = lambda bpm, beats: None) -> None:
        self.practice = practice
        self.metronome = metronome  # (bpm, 一小节几拍);bpm 0 = 停

    def handle(self, text: str) -> str | None:
        raw = re.sub(r"\s+", " ", str(text or "")).strip()
        compact = raw.replace(" ", "")
        if METRO_STOP.match(compact):
            self.metronome(0, 4)
            return "节拍器停了。"
        match = METRO.match(raw) or METRO.match(compact)
        if match:
            bpm = int(match.group("bpm") or 80)
            if not 30 <= bpm <= 300:
                return "节拍器的速度要在 30 到 300 之间。"
            beats_text = (match.group("beats") or "4").strip()[0]
            beats = int(beats_text) if beats_text.isdigit() else CN_NUM.get(beats_text, 4)
            self.metronome(bpm, beats)
            return f"节拍器 {bpm} BPM,{beats} 拍(说“节拍器停”就停)。"
        match = LOG.match(compact) or LOG2.match(compact)
        if match and ("吉他" in compact or "琴" in compact or "guitar" in compact.lower()):
            amount = _num(match.group("n"))
            minutes = amount * 60 if match.group("u") in ("小时", "钟头") else amount
            if minutes <= 0:
                return None
            return self.practice.log(minutes)
        if STATS.match(compact):
            return self.practice.report()
        return None
