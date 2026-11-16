"""智能家居自动规则:用一句话设,星期五到时自己开关 Tapo 灯和插座(电脑开着时;不问 Claude、不花钱)。

说法例子:
  “每天晚上 12 点关所有插座”“平日早上 7 点开卧室灯”“周末下午 3 点半关客厅灯”
  “太阳下山开客厅灯”“日落前半小时开灯”“天亮关灯”
  “我离开电脑 30 分钟就关桌灯”“我回到电脑前开桌灯”
  “家里的规则”看全部;“删掉规则 2”“暂停规则 2”“恢复规则 2”。
日出日落时间看默认城市的天气预报(Open-Meteo),拿不到就用吉隆坡大约的时间。
存在 friday_homerules.json。
"""

from __future__ import annotations

import datetime
import json
import os
import re
import threading
from pathlib import Path
from typing import Callable

MAX_RULES = 30
LATE_LIMIT = 30  # 分钟:电脑晚开了,超过这么久的就不补做
AWAY_RESET = 60  # 秒:碰了电脑就算回来

CN = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
WEEKDAY_WORDS = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
DAYS_TEXT = {"daily": "每天", "weekdays": "平日", "weekends": "周末"}

TIME = re.compile(r"(?P<part>凌晨|早上|上午|中午|下午|傍晚|晚上|半夜)?\s*(?P<h>\d{1,2}|[零一二两三四五六七八九十]{1,3})\s*[点點:：]"
                  r"\s*(?P<m>半|一刻|三刻|\d{1,2}|[零一二三四五六七八九十]{1,3})?\s*(分)?")
HHMM = re.compile(r"(?<!\d)(?P<h>[01]?\d|2[0-3]):(?P<m>[0-5]\d)(?!\d)")
SUN = re.compile(r"(?P<which>太阳下山|天黑|日落|太阳出来|天亮|日出)(?P<rel>前|后)?\s*(?P<n>\d{1,3}|[一二两三四五六七八九十]{1,3}|半)?\s*(个)?(?P<u>小时|钟头|分钟|分)?")
AWAY = re.compile(r"(我)?(离开|不在)(电脑)?(前)?\s*(?P<n>\d{1,3}|[一二两三四五六七八九十]{1,3}|半)\s*(个)?(?P<u>小时|钟头|分钟|分)")
BACK = re.compile(r"(回到|坐回)(电脑|座位)|我回来(的时候|时)")
# 动作在句子最后面(“离开”里的“开”不算):……(就)开 / 关 + 设备
ACTION = re.compile(r"^(?P<head>.*?)(就|的时候|时)?\s*(帮我)?(?P<do>打开|开|关掉|关上|关)(?P<what>[^开关]{1,20}?)[。!！]?$")
DAYS = re.compile(r"(每天|天天|平日|工作日|周一到周五|星期一到星期五|周末|星期六日|每个?星期(?P<w1>[一二三四五六日天])|每周(?P<w2>[一二三四五六日天]))")
LIST = re.compile(r"^(星期五)?[,，\s]*(家里|智能家居|灯|插座)?(的)?(自动)?(规则|自动化)(有哪些|列表|清单)?[?？]?$")
EDIT = re.compile(r"^(星期五)?[,，\s]*(?P<op>删掉|删除|取消|暂停|停用|恢复|启用|打开)(第)?\s*(?P<n>\d{1,2})\s*(个|条)?(规则|自动化)\s*(?P<n2>\d{1,2})?[。!！]?$"
                  r"|^(星期五)?[,，\s]*(?P<op2>删掉|删除|取消|暂停|停用|恢复|启用)(规则|自动化)\s*(?P<n3>\d{1,2})[。!！]?$")


def _num(text: str | None) -> int:
    if not text:
        return 0
    if text.isdigit():
        return int(text)
    if text == "半":
        return 30
    if text == "十":
        return 10
    if text.startswith("十"):
        return 10 + CN.get(text[1:], 0)
    if "十" in text:
        tens, _, ones = text.partition("十")
        return CN.get(tens, 1) * 10 + (CN.get(ones, 0) if ones else 0)
    return CN.get(text, 0)


def parse_time(text: str) -> str | None:
    """“晚上 12 点”“下午三点半”“7:30” -> 'HH:MM'。"""
    match = HHMM.search(text)
    if match:
        return f"{int(match.group('h')):02d}:{match.group('m')}"
    match = TIME.search(text)
    if not match:
        return None
    hour = _num(match.group("h"))
    minute_text = match.group("m")
    minute = {"半": 30, "一刻": 15, "三刻": 45}.get(minute_text or "", None)
    if minute is None:
        minute = _num(minute_text) if minute_text else 0
    part = match.group("part") or ""
    if part in ("下午", "傍晚", "晚上") and hour < 12:
        hour += 12
    if part == "中午" and hour < 11:
        hour += 12
    if part in ("晚上", "半夜") and hour == 24 or (part in ("半夜", "晚上") and hour == 12):
        hour = 0  # 晚上 12 点 = 半夜 00:00
    if part in ("凌晨", "早上", "上午") and hour == 12:
        hour = 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return f"{hour:02d}:{minute:02d}"


def parse_days(text: str) -> str | list[int]:
    match = DAYS.search(text)
    if not match:
        return "daily"
    word = match.group(1)
    if word in ("平日", "工作日", "周一到周五", "星期一到星期五"):
        return "weekdays"
    if word in ("周末", "星期六日"):
        return "weekends"
    day = match.group("w1") or match.group("w2")
    return [WEEKDAY_WORDS[day]] if day else "daily"


def parse(text: str) -> dict | None:
    """一句话 -> 规则 {trigger, days, do, what};看不懂(或不是规则)返回 None。
    只有时间的(“下午三点关灯”)当成一次性的,不当规则;要说“每天 / 平日 / 周末 / 规则”才算。"""
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    explicit = bool(DAYS.search(text) or re.search(r"规则|自动化|自动", text))
    text = re.sub(r"^(星期五)?[,，\s]*(帮我)?(设|加|新增|建)?(一个|一条)?(自动)?(规则|自动化)?[:：,，\s]*", "", text)
    action = None
    for found in re.finditer(r"打开|开|关掉|关上|关", text):  # 找最后一个开 / 关
        candidate = ACTION.match(text[:found.start()] + text[found.start():])
        tail = text[found.start():]
        m = re.match(r"(?P<do>打开|开|关掉|关上|关)(?P<what>[^开关]{1,20}?)[。!！]?$", tail)
        if m and candidate:
            action = (text[:found.start()], m)
    if action is None:
        return None
    head, m = action
    head = re.sub(r"(就|的时候|(?<!小)时)\s*(帮我)?\s*$", "", head)  # “一个小时”的“时”不能拿掉
    what = m.group("what").strip(" 的把")
    if not what:
        return None
    on = m.group("do") in ("打开", "开")
    rule: dict = {"do": "on" if on else "off", "what": what}
    away = AWAY.search(head)
    sun = SUN.search(head)
    if away:
        minutes = _num(away.group("n")) * (60 if away.group("u") in ("小时", "钟头") else 1)
        if away.group("n") == "半" and away.group("u") in ("小时", "钟头"):
            minutes = 30
        if not 1 <= minutes <= 600:
            return None
        rule["trigger"] = {"type": "away", "minutes": minutes}
    elif BACK.search(head) and not parse_time(head):
        rule["trigger"] = {"type": "back"}
    elif sun:
        offset = _num(sun.group("n")) if sun.group("n") else 0
        if sun.group("n") == "半" and sun.group("u") in ("小时", "钟头"):
            offset = 30
        elif sun.group("u") in ("小时", "钟头"):
            offset *= 60
        if sun.group("rel") == "前":
            offset = -offset
        which = "sunset" if sun.group("which") in ("太阳下山", "天黑", "日落") else "sunrise"
        rule["trigger"] = {"type": which, "offset": offset}
        rule["days"] = parse_days(head)
    else:
        at = parse_time(head)
        if at is None or not explicit:
            return None
        rule["trigger"] = {"type": "time", "at": at}
        rule["days"] = parse_days(head)
    return rule


def describe_rule(rule: dict) -> str:
    trig = rule["trigger"]
    days = rule.get("days", "daily")
    day_text = DAYS_TEXT.get(days, "") if isinstance(days, str) else "每周" + "、".join("一二三四五六日"[d] for d in days)
    if trig["type"] == "time":
        when = f"{day_text} {trig['at']}"
    elif trig["type"] in ("sunset", "sunrise"):
        name = "日落" if trig["type"] == "sunset" else "日出"
        off = trig.get("offset", 0)
        when = f"{day_text}{name}" + (f"{'前' if off < 0 else '后'} {abs(off)} 分钟" if off else "")
    elif trig["type"] == "away":
        when = f"离开电脑 {trig['minutes']} 分钟"
    else:
        when = "回到电脑前"
    return f"{when} {'开' if rule['do'] == 'on' else '关'}{rule['what']}"


class HomeRules:
    def __init__(self, path: str | os.PathLike, switch: Callable[[str, bool], str | None],
                 sun: Callable[[datetime.date], tuple[datetime.datetime, datetime.datetime] | None] = lambda d: None,
                 idle: Callable[[], float | None] = lambda: None,
                 clock: Callable[[], datetime.datetime] = datetime.datetime.now) -> None:
        self.path = Path(path)
        self.switch = switch  # (哪个设备, 开不开) -> 结果的话;没有这个设备 None
        self.sun = sun
        self.idle = idle
        self.clock = clock
        self.lock = threading.Lock()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        self.rules: list[dict] = [r for r in data.get("rules") or [] if isinstance(r, dict) and "trigger" in r]
        self.away_since: float | None = None  # 离开电脑从哪一刻开始(秒数)
        self.was_away = False

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"rules": self.rules}, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            pass

    def add(self, rule: dict) -> str:
        with self.lock:
            if len(self.rules) >= MAX_RULES:
                raise ValueError(f"规则最多 {MAX_RULES} 条,先删掉一些")
            text = describe_rule(rule)
            if any(describe_rule(r) == text for r in self.rules):
                return f"已经有这条规则了:{text}"
            rule = {**rule, "id": max((r.get("id", 0) for r in self.rules), default=0) + 1, "enabled": True, "last": ""}
            self.rules.append(rule)
            self._save()
        return f"好,规则 {rule['id']}:{text}(电脑开着时我会照做)。"

    def find(self, number: int) -> dict | None:
        return next((r for r in self.rules if r.get("id") == number), None)

    def remove(self, number: int) -> str:
        with self.lock:
            rule = self.find(number)
            if rule is None:
                return f"没有规则 {number}。"
            self.rules.remove(rule)
            self._save()
        return f"删掉了规则 {number}:{describe_rule(rule)}。"

    def set_enabled(self, number: int, on: bool) -> str:
        with self.lock:
            rule = self.find(number)
            if rule is None:
                return f"没有规则 {number}。"
            rule["enabled"] = on
            self._save()
        return f"规则 {number} {'恢复了' if on else '暂停了'}:{describe_rule(rule)}。"

    def describe(self) -> str:
        if not self.rules:
            return "还没有家里的自动规则。可以说“每天晚上 12 点关所有插座”“太阳下山开客厅灯”“我离开电脑 30 分钟就关桌灯”。"
        return "家里的自动规则:\n" + "\n".join(
            f"{r['id']}. {describe_rule(r)}{'' if r.get('enabled', True) else '(暂停中)'}" for r in self.rules)

    # ---------- 到时间就做 ----------
    @staticmethod
    def _day_ok(rule: dict, day: datetime.date) -> bool:
        days = rule.get("days", "daily")
        if days == "weekdays":
            return day.weekday() < 5
        if days == "weekends":
            return day.weekday() >= 5
        if isinstance(days, list):
            return day.weekday() in days
        return True

    def _when(self, rule: dict, day: datetime.date) -> datetime.datetime | None:
        trig = rule["trigger"]
        if trig["type"] == "time":
            hour, minute = (int(x) for x in trig["at"].split(":"))
            return datetime.datetime.combine(day, datetime.time(hour, minute))
        if trig["type"] in ("sunset", "sunrise"):
            times = None
            try:
                times = self.sun(day)
            except Exception:
                times = None
            if times is None:  # 拿不到:用吉隆坡大约的时间
                times = (datetime.datetime.combine(day, datetime.time(7, 5)),
                         datetime.datetime.combine(day, datetime.time(19, 15)))
            base = times[1] if trig["type"] == "sunset" else times[0]
            return base.replace(tzinfo=None) + datetime.timedelta(minutes=trig.get("offset", 0))
        return None

    def tick(self) -> list[str]:
        """每 30 秒看一次;做了什么就返回要说的话。"""
        now = self.clock()
        said = []
        idle = None
        try:
            idle = self.idle()
        except Exception:
            idle = None
        away_rules = [r for r in self.rules if r["trigger"]["type"] in ("away", "back")]
        if idle is not None and away_rules:
            if idle >= AWAY_RESET:
                if self.away_since is None:
                    self.away_since = now.timestamp() - idle
            else:
                if self.away_since is not None and now.timestamp() - self.away_since >= 10 * 60:
                    self.was_away = True  # 离开 10 分钟以上回来,才算“回到电脑前”
                self.away_since = None
        for rule in list(self.rules):
            if not rule.get("enabled", True):
                continue
            trig = rule["trigger"]
            key = ""
            if trig["type"] in ("time", "sunset", "sunrise"):
                for day in (now.date(), now.date() - datetime.timedelta(days=1)):
                    when = self._when(rule, day)
                    if when is None or not self._day_ok(rule, day):
                        continue
                    if when <= now <= when + datetime.timedelta(minutes=LATE_LIMIT) and rule.get("last") != day.isoformat():
                        key = day.isoformat()
                        break
            elif trig["type"] == "away" and self.away_since is not None:
                if now.timestamp() - self.away_since >= trig["minutes"] * 60 and rule.get("last") != str(int(self.away_since)):
                    key = str(int(self.away_since))
            elif trig["type"] == "back" and self.was_away:
                key = now.strftime("%Y-%m-%d %H:%M")
            if not key:
                continue
            rule["last"] = key
            try:
                result = self.switch(rule["what"], rule["do"] == "on")
            except Exception as e:  # noqa: BLE001
                result = f"没做成:{e}"
            said.append(f"🏠 规则 {rule['id']}({describe_rule(rule)}):" + (result or f"找不到“{rule['what']}”这个设备"))
        if self.was_away:
            self.was_away = False
        if said:
            with self.lock:
                self._save()
        return said


def sun_times(fetch=None) -> Callable[[datetime.date], tuple[datetime.datetime, datetime.datetime] | None]:
    """默认城市的日出、日落(Open-Meteo;每天查一次)。"""
    import friday_alerts
    import friday_weather

    cache: dict = {}

    def get(day: datetime.date):
        if day.isoformat() not in cache:
            place = friday_weather.find_place(friday_alerts.rain_place(), fetch or friday_weather.fetch_json)
            data = friday_weather.forecast(place, 3, fetch or friday_weather.fetch_json).get("daily") or {}
            for d, rise, sset in zip(data.get("time") or [], data.get("sunrise") or [], data.get("sunset") or []):
                cache[d] = (datetime.datetime.fromisoformat(rise), datetime.datetime.fromisoformat(sset))
        return cache.get(day.isoformat())

    return get


class Commands:
    def __init__(self, rules: HomeRules) -> None:
        self.rules = rules

    def handle(self, text: str) -> str | None:
        compact = re.sub(r"\s+", "", str(text or ""))
        if LIST.match(compact):
            return self.rules.describe()
        match = EDIT.match(compact)
        if match:
            op = match.group("op") or match.group("op2")
            number = int(match.group("n") or match.group("n2") or match.group("n3"))
            if op in ("删掉", "删除", "取消"):
                return self.rules.remove(number)
            return self.rules.set_enabled(number, op in ("恢复", "启用", "打开"))
        rule = parse(text)
        if rule is None:
            return None
        try:
            return self.rules.add(rule)
        except ValueError as e:
            return str(e)
