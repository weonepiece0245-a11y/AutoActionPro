"""待办自动排进日历:说“帮我安排这周的待办”,星期五看你 Google 日历里的空档,把待办按截止日期和大概要多久排进去,
先给你看安排,你说“就这样安排”(或按按钮)才放进日历;到时间没做完的,自动挪到下一个空档,并告诉你。

只排在 齿轮设定 → 生活 → 待办排进日历 设的时段里(默认 09:00~22:00),一天最多排 3 件,两件之间留 15 分钟。
要多久:待办里写了(例如“写报告 2小时”)就照写的,没写请 Haiku 估(一次大约 RM0.005),估不出来算 1 小时。
日历里的活动标题前面有 📝,是星期五排的;你自己在日历上拖去别的时间也可以。存在 friday_plan.json。
"""

from __future__ import annotations

import datetime
import json
import os
import re
import threading
from pathlib import Path
from typing import Callable

MODEL = "claude-haiku-5-5"
MAX_PER_DAY = 3
GAP_MINUTES = 15
MIN_SLOT = 30
MAX_MOVES = 5
DEFAULT_MINUTES = 60

PLAN = re.compile(r"^(星期五)?[,，\s]*(帮我|请)?(安排|规划|排|计划)(一下)?(?P<when>这周|这个星期|本周|今天|明天|接下来几天|未来一周|下周|这几天)?(的)?"
                  r"(待办|待办事项|要做的事|事情|工作)(排进日历|放进日历|到日历|进日历|在日历上)?[?？。!！]?$"
                  r"|^(星期五)?[,，\s]*(帮我)?把(?P<when2>这周|今天|明天|下周)?(的)?待办(都)?(排进|放进|安排进|安排到)(日历|行事历)(里)?[。!！]?$")
APPLY = re.compile(r"^(星期五)?[,，\s]*(好|可以|行|ok|OK|好的)?[,，\s]*(就这样安排|放进日历|加进日历|排进去|排进日历|确定安排|照这个安排|就这样)(吧)?[。!！]?$")
CANCEL = re.compile(r"^(星期五)?[,，\s]*(不用安排了|取消安排|不要安排|算了不用排)[。!！]?$")
STATUS = re.compile(r"^(星期五)?[,，\s]*(我|你)?(帮我)?(安排了|排了)(什么|哪些)(待办)?[?？]?$")
DURATION = re.compile(r"(?P<n>\d+(?:\.\d+)?|[一二两三四五六七八九十]+)?\s*个?(?P<half>半)?\s*(?P<u>小时|钟头|hr|h|分钟|分|min)", re.I)

ESTIMATE_PROMPT = """估计每件待办大概要多少分钟(一个人专心做)。只回答数字;很快的(打个电话、回个讯息)写 15~30,
要出门办的事包括来回路程,写报告、做功课这类 60~180。今天是 {today}。"""
ESTIMATE_SCHEMA = {
    "type": "object",
    "properties": {"minutes": {"type": "array", "items": {"type": "integer"}}},
    "required": ["minutes"], "additionalProperties": False,
}


def hours_setting() -> tuple[datetime.time, datetime.time]:
    raw = os.environ.get("FRIDAY_PLAN_HOURS", "09:00-22:00")
    try:
        a, b = raw.split("-")
        start, end = datetime.time.fromisoformat(a.strip()), datetime.time.fromisoformat(b.strip())
        if start < end:
            return start, end
    except ValueError:
        pass
    return datetime.time(9), datetime.time(22)


def written_minutes(title: str) -> int | None:
    """待办里写了要多久:“写报告 2小时”“打电话 15分钟”“半小时”。"""
    import friday_focus

    for found in DURATION.finditer(title or ""):
        number, half = found.group("n"), bool(found.group("half"))
        if not number and not half:
            continue
        hours = found.group("u").lower() in ("小时", "钟头", "h", "hr")
        if not number:
            value = 0.0
        elif re.fullmatch(r"\d+(\.\d+)?", number):
            value = float(number)
        else:
            value = friday_focus.cn_number(number + "分钟")
            if value is None:
                continue
        minutes = round(value * 60 + (30 if half else 0)) if hours else round(value)
        if 5 <= minutes <= 8 * 60:
            return minutes
    return None


def span(when: str, now: datetime.datetime) -> tuple[datetime.datetime, datetime.datetime]:
    """“这周 / 今天 / 明天 / 下周”-> 要排的时间范围。"""
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if when == "今天":
        return now, day + datetime.timedelta(days=1)
    if when == "明天":
        return day + datetime.timedelta(days=1), day + datetime.timedelta(days=2)
    if when in ("这周", "这个星期", "本周"):
        end = day + datetime.timedelta(days=7 - day.weekday())
        return now, end if (end - now).days >= 1 else end + datetime.timedelta(days=7)
    if when == "下周":
        start = day + datetime.timedelta(days=7 - day.weekday())
        return start, start + datetime.timedelta(days=7)
    return now, day + datetime.timedelta(days=7)


def free_slots(busy: list[tuple[datetime.datetime, datetime.datetime]], start: datetime.datetime, end: datetime.datetime,
               hours: tuple[datetime.time, datetime.time] | None = None, min_minutes: int = MIN_SLOT) -> list[list]:
    """每天设定时段里,没有活动的空档:[[开始, 结束], ...]。"""
    first, last = hours or hours_setting()
    busy = sorted((s, e) for s, e in busy if e > start and s < end)
    slots = []
    day = start.replace(hour=0, minute=0, second=0, microsecond=0)
    while day < end:
        open_at = max(day.replace(hour=first.hour, minute=first.minute), start)
        close_at = min(day.replace(hour=last.hour, minute=last.minute), end)
        cursor = open_at
        for s, e in busy:
            if e <= cursor or s >= close_at:
                continue
            if (s - cursor).total_seconds() >= min_minutes * 60:
                slots.append([cursor, s])
            cursor = max(cursor, e)
        if (close_at - cursor).total_seconds() >= min_minutes * 60:
            slots.append([cursor, close_at])
        day += datetime.timedelta(days=1)
    return slots


def round_up(moment: datetime.datetime, minutes: int = 15) -> datetime.datetime:
    extra = (-moment.minute) % minutes
    moment = moment.replace(second=0, microsecond=0) + datetime.timedelta(minutes=extra)
    return moment


def schedule(tasks: list[dict], slots: list[list], per_day: dict | None = None) -> tuple[list[dict], list[dict]]:
    """把待办放进空档:截止早的先、要久的先;不超过截止日;一天最多 3 件。返回 (排好的, 排不下的)。"""
    per_day = dict(per_day or {})
    slots = [[round_up(s), e] for s, e in slots]
    order = sorted(tasks, key=lambda t: (t.get("due") or datetime.date.max, -t["minutes"]))
    placed, left = [], []
    for task in order:
        need = datetime.timedelta(minutes=task["minutes"])
        for slot in slots:
            start, end = slot
            day = start.date()
            if per_day.get(day, 0) >= MAX_PER_DAY or end - start < need:
                continue
            if task.get("due") and day > task["due"]:
                continue
            placed.append({**task, "start": start, "end": start + need})
            per_day[day] = per_day.get(day, 0) + 1
            slot[0] = round_up(start + need + datetime.timedelta(minutes=GAP_MINUTES))
            break
        else:
            left.append(task)
    placed.sort(key=lambda p: p["start"])
    return placed, left


WEEK = "一二三四五六日"


def when_text(start: datetime.datetime, now: datetime.datetime) -> str:
    days = (start.date() - now.date()).days
    day = {0: "今天", 1: "明天", 2: "后天"}.get(days, f"{start.month}/{start.day}(星期{WEEK[start.weekday()]})")
    return f"{day} {start:%H:%M}"


class Planner:
    def __init__(self, path: str | os.PathLike, clock: Callable[[], datetime.datetime] | None = None) -> None:
        self.path = Path(path)
        self.clock = clock or (lambda: datetime.datetime.now().astimezone())
        self.lock = threading.Lock()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        data = data if isinstance(data, dict) else {}
        self.proposal: list[dict] = list(data.get("proposal") or [])
        self.left: list[str] = list(data.get("left") or [])
        self.entries: list[dict] = list(data.get("entries") or [])

    def _save(self) -> None:
        temp = self.path.with_name(self.path.name + ".tmp")
        temp.write_text(json.dumps({"proposal": self.proposal, "left": self.left, "entries": self.entries},
                                   ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(temp, self.path)

    @staticmethod
    def _dump(item: dict) -> dict:
        return {**{k: v for k, v in item.items() if k not in ("start", "end", "due")},
                "start": item["start"].isoformat(), "end": item["end"].isoformat(),
                "due": item["due"].isoformat() if item.get("due") else ""}

    # ---------- 提安排 ----------
    def propose(self, tasks: list[dict], busy: list, when: str = "") -> str:
        """tasks:[{key, source, title, due(date|None), minutes}];busy:日历上已经有的 [(开始, 结束)]。"""
        now = self.clock()
        if not tasks:
            with self.lock:
                self.proposal, self.left = [], []
                self._save()
            return "待办清单是空的(或者都做完了),没有要排的。"
        start, end = span(when, now)
        mine = [(datetime.datetime.fromisoformat(e["start"]), datetime.datetime.fromisoformat(e["end"])) for e in self.entries]
        planned_keys = {(e["source"], e["key"]) for e in self.entries}
        todo = [t for t in tasks if (t["source"], t["key"]) not in planned_keys]
        if not todo:
            return "待办都已经排进日历了。说“我安排了什么”看看。"
        per_day: dict = {}
        for s, _ in mine:
            per_day[s.date()] = per_day.get(s.date(), 0) + 1
        placed, left = schedule(todo, free_slots(list(busy) + mine, start, end), per_day)
        with self.lock:
            self.proposal = [self._dump(p) for p in placed]
            self.left = [t["title"] for t in left]
            self._save()
        if not placed:
            return "这段时间日历里没有够长的空档,排不进去。可以说“帮我安排下周的待办”,或在设定里把可以排的时段调长一点。"
        lines = [f"- {when_text(p['start'], now)}~{p['end']:%H:%M} {p['title']}" for p in placed]
        text = "这样安排好吗?\n" + "\n".join(lines)
        if left:
            text += "\n排不下:" + "、".join(t["title"] for t in left[:6]) + ("(截止前没有够长的空档)" if any(t.get("due") for t in left) else "")
        return text + "\n说“就这样安排”我就放进日历;不要的话说“取消安排”。"

    # ---------- 放进日历 ----------
    def apply(self, create_event: Callable[[str, datetime.datetime, datetime.datetime], str]) -> str:
        with self.lock:
            proposal = list(self.proposal)
        if not proposal:
            return "现在没有要放进日历的安排。先说“帮我安排这周的待办”。"
        done, failed = [], []
        for p in proposal:
            start, end = datetime.datetime.fromisoformat(p["start"]), datetime.datetime.fromisoformat(p["end"])
            try:
                event_id = create_event(p["title"], start, end)
            except Exception:  # noqa: BLE001
                failed.append(p["title"])
                continue
            done.append({**p, "event_id": event_id, "moves": 0})
        with self.lock:
            self.entries += done
            self.proposal, self.left = [], []
            self._save()
        text = f"放进日历了({len(done)} 件),标题前面有 📝。做完在待办清单打勾;没做完我会挪到下一个空档。"
        if failed:
            text += "没放成:" + "、".join(failed)
        return text

    def cancel(self) -> str:
        with self.lock:
            had = bool(self.proposal)
            self.proposal, self.left = [], []
            self._save()
        return "好,不安排了。" if had else "现在没有待定的安排。"

    # ---------- 没做完就挪 ----------
    def follow_up(self, is_done: Callable[[str, str], bool | None], busy: Callable[[datetime.datetime, datetime.datetime], list],
                  move_event: Callable[[str, datetime.datetime, datetime.datetime], None]) -> list[str]:
        """每小时:做完的从计划拿掉;时间过了还没做完的,挪到下一个空档。返回要告诉你的话。"""
        now = self.clock()
        notes = []
        with self.lock:
            entries = list(self.entries)
        keep = []
        for entry in entries:
            done = is_done(entry["source"], entry["key"])
            if done:
                continue  # 做完了(或待办删掉了)
            end = datetime.datetime.fromisoformat(entry["end"])
            if end > now or done is None:
                keep.append(entry)
                continue
            if entry.get("moves", 0) >= MAX_MOVES:
                notes.append(f"“{entry['title']}”已经挪了 {MAX_MOVES} 次还没做,我先不排了,要的话再说“帮我安排待办”。")
                continue
            minutes = int((end - datetime.datetime.fromisoformat(entry["start"])).total_seconds() // 60)
            others = [(datetime.datetime.fromisoformat(e["start"]), datetime.datetime.fromisoformat(e["end"]))
                      for e in keep + [x for x in entries if x is not entry]]
            window_end = now + datetime.timedelta(days=7)
            due = datetime.date.fromisoformat(entry["due"]) if entry.get("due") else None
            placed, _ = schedule([{"title": entry["title"], "minutes": minutes, "due": due}],
                                 free_slots(list(busy(now, window_end)) + others, now, window_end))
            if not placed:
                notes.append(f"“{entry['title']}”没做完,接下来一周找不到空档再排,要不要调整一下?")
                keep.append(entry)
                continue
            new = placed[0]
            try:
                move_event(entry["event_id"], new["start"], new["end"])
            except Exception:  # noqa: BLE001
                keep.append(entry)
                continue
            entry = {**entry, "start": new["start"].isoformat(), "end": new["end"].isoformat(), "moves": entry.get("moves", 0) + 1}
            keep.append(entry)
            notes.append(f"“{entry['title']}”还没做完,挪到 {when_text(new['start'], now)}。")
        with self.lock:
            self.entries = keep
            self._save()
        return notes

    def describe(self) -> str:
        now = self.clock()
        if self.proposal:
            return "有一个还没确认的安排:\n" + "\n".join(
                f"- {when_text(datetime.datetime.fromisoformat(p['start']), now)} {p['title']}" for p in self.proposal) \
                + "\n说“就这样安排”放进日历。"
        if not self.entries:
            return "现在没有星期五排进日历的待办。说“帮我安排这周的待办”。"
        items = sorted(self.entries, key=lambda e: e["start"])
        return "排进日历的待办:\n" + "\n".join(
            f"- {when_text(datetime.datetime.fromisoformat(e['start']), now)} {e['title']}" for e in items)

    def state(self) -> dict:
        return {"proposal": self.proposal, "left": self.left, "entries": sorted(self.entries, key=lambda e: e["start"]),
                "hours": "-".join(t.strftime("%H:%M") for t in hours_setting())}


def estimator(client, on_usage=None, today: Callable[[], str] = lambda: datetime.date.today().isoformat()):
    """请 Haiku 估每件待办要多少分钟。"""
    def estimate(titles: list[str]) -> list[int]:
        response = client.messages.create(
            model=MODEL, max_tokens=300, system=ESTIMATE_PROMPT.format(today=today()),
            messages=[{"role": "user", "content": "\n".join(f"{i}. {t[:80]}" for i, t in enumerate(titles, 1))}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": ESTIMATE_SCHEMA}},
        )
        if on_usage is not None and getattr(response, "usage", None) is not None:
            try:
                on_usage(response.usage, MODEL)
            except Exception:
                pass
        data = json.loads(next(b.text for b in response.content if getattr(b, "type", "") == "text"))
        return [int(m) for m in data.get("minutes") or []]
    return estimate


def with_minutes(tasks: list[dict], estimate: Callable[[list[str]], list[int]] | None) -> list[dict]:
    """补上每件要多久:写了的照写,没写的请 Haiku 估,估不出来 1 小时。"""
    unknown = [t for t in tasks if written_minutes(t["title"]) is None]
    guesses: list[int] = []
    if unknown and estimate is not None:
        try:
            guesses = estimate([t["title"] for t in unknown])
        except Exception:  # noqa: BLE001
            guesses = []
    guess = dict(zip((id(t) for t in unknown), guesses))
    out = []
    for t in tasks:
        minutes = written_minutes(t["title"]) or guess.get(id(t)) or DEFAULT_MINUTES
        out.append({**t, "minutes": max(15, min(int(minutes), 6 * 60))})
    return out


class Commands:
    def __init__(self, planner: Planner, propose: Callable[[str], str], apply: Callable[[], str]) -> None:
        self.planner, self.propose, self.apply = planner, propose, apply

    def handle(self, text: str) -> str | None:
        text = str(text or "").strip()
        found = PLAN.match(text)
        if found:
            return self.propose(found.group("when") or found.group("when2") or "")
        if APPLY.match(text) and self.planner.proposal:
            return self.apply()
        if CANCEL.match(text):
            return self.planner.cancel()
        if STATUS.match(text):
            return self.planner.describe()
        return None
