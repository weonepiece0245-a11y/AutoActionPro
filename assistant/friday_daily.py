"""“星期五”的日常能力:提醒和定时任务、待办清单。

都在本地运行,不需要注册任何账号。提醒和待办保存在 JSON 文件里,关掉程序也不会丢。
提醒只在“星期五”开着的时候才会响(窗口最小化也可以)。
"""

from __future__ import annotations

import datetime
import json
import os
import queue
import threading
from pathlib import Path
from typing import Callable

TIME_FORMAT = "%Y-%m-%d %H:%M"
REPEATS = {"none": "一次", "daily": "每天", "weekdays": "每个工作日", "weekly": "每周"}
KINDS = {"notify": "提醒", "task": "定时任务"}
MISSED_AFTER = datetime.timedelta(minutes=2)  # 晚了这么久才响,就标注“错过了”

Clock = Callable[[], datetime.datetime]


def _now() -> datetime.datetime:
    return datetime.datetime.now().replace(second=0, microsecond=0)


def parse_time(text: str) -> datetime.datetime:
    text = text.strip().replace("T", " ").replace("/", "-")
    try:
        return datetime.datetime.strptime(text[:16], TIME_FORMAT)
    except ValueError:
        raise ValueError(f"时间格式不对:“{text}”,请用 YYYY-MM-DD HH:MM,例如 2026-11-07 09:00") from None


def _load(path: Path, key: str) -> list[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError):
        try:
            os.replace(path, path.with_name(path.name + ".broken"))
        except OSError:
            pass
        return []
    items = data.get(key) if isinstance(data, dict) else None
    return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []


def _save(path: Path, key: str, items: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps({key: items}, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _match(items: list[dict], key: str, field: str) -> list[dict]:
    """按编号(3 或 #3)或关键词找条目。"""
    key = key.strip().lstrip("#")
    if key.isdigit():
        return [i for i in items if i.get("id") == int(key)]
    return [i for i in items if key and key in i.get(field, "")]


def _next_id(items: list[dict]) -> int:
    return max((i.get("id", 0) for i in items), default=0) + 1


# ---------------- 提醒和定时任务 ----------------


def next_occurrence(when: datetime.datetime, repeat: str, after: datetime.datetime) -> datetime.datetime:
    """重复提醒的下一次时间(严格晚于 after)。"""
    step = datetime.timedelta(days=7 if repeat == "weekly" else 1)
    when += step
    while when <= after or (repeat == "weekdays" and when.weekday() >= 5):
        when += datetime.timedelta(days=1) if repeat == "weekdays" else step
    return when


class Reminders:
    """提醒列表,保存在 JSON 文件里。可以被聊天线程和提醒线程同时使用。"""

    def __init__(self, path: str | os.PathLike, clock: Clock = _now) -> None:
        self.path = Path(path)
        self.clock = clock
        self.lock = threading.Lock()
        self.items = _load(self.path, "reminders")
        self._skip_missed_repeats()

    def _skip_missed_repeats(self) -> None:
        """程序关着时错过的重复提醒不再补响(比如昨天的早报),直接排到下一次。"""
        now = self.clock()
        changed = False
        for item in self.items:
            if item.get("repeat", "none") != "none" and parse_time(item["time"]) < now - MISSED_AFTER:
                item["time"] = next_occurrence(parse_time(item["time"]), item["repeat"], now).strftime(TIME_FORMAT)
                changed = True
        if changed:
            _save(self.path, "reminders", self.items)

    def add(self, time_text: str, message: str, kind: str = "notify", repeat: str = "none") -> dict:
        when = parse_time(time_text)
        message = " ".join(message.split())
        if not message:
            raise ValueError("提醒内容是空的")
        if kind not in KINDS:
            raise ValueError(f"kind 只能是 {'、'.join(KINDS)}")
        if repeat not in REPEATS:
            raise ValueError(f"repeat 只能是 {'、'.join(REPEATS)}")
        now = self.clock()
        if when < now - datetime.timedelta(minutes=1):
            if repeat == "none":
                raise ValueError(f"{when:%Y-%m-%d %H:%M} 已经过去了(现在是 {now:%Y-%m-%d %H:%M})")
            when = next_occurrence(when, repeat, now)
        elif repeat == "weekdays" and when.weekday() >= 5:
            when = next_occurrence(when, repeat, when)  # 工作日提醒落在周末时,顺延到周一
        with self.lock:
            item = {
                "id": _next_id(self.items),
                "time": when.strftime(TIME_FORMAT),
                "message": message,
                "kind": kind,
                "repeat": repeat,
            }
            self.items.append(item)
            _save(self.path, "reminders", self.items)
        return item

    def cancel(self, key: str) -> list[dict]:
        with self.lock:
            removed = _match(self.items, key, "message")
            if removed:
                self.items = [i for i in self.items if i not in removed]
                _save(self.path, "reminders", self.items)
        return removed

    def set_field(self, number: int, key: str, value) -> None:
        """给某条提醒加一项资料(例如 Google 日历里对应的活动 id)。"""
        with self.lock:
            for item in self.items:
                if item.get("id") == number:
                    item[key] = value
                    _save(self.path, "reminders", self.items)
                    return

    def upcoming(self) -> list[dict]:
        with self.lock:
            return sorted(self.items, key=lambda i: i["time"])

    def pop_due(self) -> list[dict]:
        """取出到点的提醒;重复的排到下一次,一次性的删除。"""
        now = self.clock()
        due = []
        with self.lock:
            keep = []
            for item in self.items:
                when = parse_time(item["time"])
                if when > now:
                    keep.append(item)
                    continue
                due.append({**item, "missed": now - when > MISSED_AFTER})
                if item.get("repeat", "none") != "none":
                    keep.append({**item, "time": next_occurrence(when, item["repeat"], now).strftime(TIME_FORMAT)})
            if due:
                self.items = keep
                _save(self.path, "reminders", self.items)
        return due


def describe_reminder(item: dict) -> str:
    repeat = item.get("repeat", "none")
    when = item["time"] if repeat == "none" else f"{REPEATS[repeat]} {item['time'][11:]}(下次 {item['time']})"
    return f"#{item['id']} {when} [{KINDS.get(item.get('kind'), '提醒')}] {item['message']}"


class ReminderScheduler(threading.Thread):
    """后台每隔几秒检查一次,把到点的提醒放进事件队列,交给聊天线程处理。"""

    def __init__(self, reminders: Reminders, events: queue.Queue, interval: float = 5.0) -> None:
        super().__init__(daemon=True, name="friday-reminders")
        self.reminders = reminders
        self.events = events
        self.interval = interval
        self._stop = threading.Event()

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                for item in self.reminders.pop_due():
                    self.events.put(("reminder", item))
            except Exception:  # 文件被占用等偶发错误,下次再试
                pass
            self._stop.wait(self.interval)

    def stop(self) -> None:
        self._stop.set()


def notify_popup(title: str, message: str) -> None:
    """Windows 上弹一个置顶的提示框并响一声(不阻塞);其他系统什么也不做。"""
    if os.name != "nt":
        return

    def show() -> None:
        import ctypes
        import winsound

        winsound.MessageBeep(0x40)  # MB_ICONASTERISK
        # MB_OK | MB_ICONINFORMATION | MB_TOPMOST | MB_SETFOREGROUND
        ctypes.windll.user32.MessageBoxW(None, message, title, 0x0 | 0x40 | 0x40000 | 0x10000)

    threading.Thread(target=show, daemon=True).start()


# ---------------- 待办清单 ----------------


class Todos:
    def __init__(self, path: str | os.PathLike, clock: Clock = _now) -> None:
        self.path = Path(path)
        self.clock = clock
        self.items = _load(self.path, "todos")

    def _save(self) -> None:
        _save(self.path, "todos", self.items)

    def add(self, text: str, due: str | None = None) -> dict:
        text = " ".join(text.split())
        if not text:
            raise ValueError("待办内容是空的")
        item = {"id": _next_id(self.items), "text": text, "done": False, "added": self.clock().strftime("%Y-%m-%d")}
        if due:
            item["due"] = due.strip()
        self.items.append(item)
        self._save()
        return item

    def pending(self) -> list[dict]:
        return [i for i in self.items if not i.get("done")]

    def complete(self, key: str) -> list[dict]:
        found = [i for i in _match(self.pending(), key, "text")]
        for item in found:
            item["done"] = True
            item["done_at"] = self.clock().strftime("%Y-%m-%d")
        if found:
            self._save()
        return found

    def delete(self, key: str) -> list[dict]:
        removed = _match(self.items, key, "text")
        if removed:
            self.items = [i for i in self.items if i not in removed]
            self._save()
        return removed

    def clear_done(self) -> int:
        before = len(self.items)
        self.items = self.pending()
        self._save()
        return before - len(self.items)


def describe_todo(item: dict) -> str:
    mark = "✓" if item.get("done") else "□"
    due = f"(截止 {item['due']})" if item.get("due") else ""
    return f"{mark} #{item['id']} {item['text']}{due}"
