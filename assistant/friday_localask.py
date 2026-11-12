"""更多事情不经过 Claude:天气、待办、提醒这几类常说的话,在电脑上直接办(更快、不花钱)。

只认很确定的说法;认不出来、或者句子比较复杂(例如“如果明天下雨就提醒我……”),照常交给 Claude。
"""

from __future__ import annotations

import datetime
import re

WEEK = "一二三四五六日天"
NUM = r"[\d零一二两三四五六七八九十]{1,3}"
LEAD = re.compile(r"^(星期五|friday|嘿|喂|好的|那|麻烦你?|请你?|帮我|给我|帮忙|可以|能不能|能)+", re.I)
PUNCT = re.compile("[\\s,，。!！?？~～、;；…]+")


def normalize(text: str) -> str:
    text = PUNCT.sub("", str(text or "")).strip()
    return LEAD.sub("", text)


def cn_int(text: str) -> int | None:
    """“8”“八”“十二”“二十” -> 数字。"""
    import friday_focus

    value = friday_focus.cn_number(text)
    return value if value is not None and 0 <= value <= 59 else None


# ======================== 提醒 ========================

RELATIVE = re.compile(r"^(?P<n>[\d.零一二两三四五六七八九十半]{1,4})个?(?P<u>分钟|分|小时|钟头)半?(以后|之后|后)")
ABSOLUTE = re.compile(
    r"^(?P<rep>每天|每日|每个?工作日|每周[" + WEEK + r"]|每个?星期[" + WEEK + r"]|每个?礼拜[" + WEEK + r"])?"
    r"(?P<day>今天|今晚|明天|明晚|明早|后天|下周[" + WEEK + r"]|下个?星期[" + WEEK + r"]|这周[" + WEEK + r"]|周[" + WEEK + r"]|星期[" + WEEK + r"])?"
    r"(?P<part>早上|早晨|上午|中午|下午|傍晚|晚上|半夜|凌晨)?"
    r"(?P<h>" + NUM + r")(点|:|：|时)(?P<m>半|一刻|三刻|" + NUM + r"分?)?(钟)?(的时候)?")
RELATIVE_HALF = re.compile(r"^(半小时|半个小时|半个钟头?|一个半小时)(以后|之后|后)")
REMIND = re.compile(r"^(?P<a>.*?)(提醒我|叫我|喊我|记得叫我)(?P<b>.*)$")
LIST = re.compile(r"^(我)?(现在)?(有)?(哪些|什么|几个|多少)?提醒(列表|清单)?(有哪些|有什么|吗)?$|^(看看|查看|列出)(我的)?提醒$|^我(的)?提醒(有哪些)?$")
CANCEL = re.compile(r"^(取消|删掉|删除|不要)(那个|这个)?(?P<k>.{1,20}?)(的)?提醒$|^(取消|删掉|删除)提醒(?P<k2>.{1,20})$")


def _weekday(char: str) -> int:
    return 6 if char in "日天" else "一二三四五六".index(char)


def parse_time_prefix(text: str, now: datetime.datetime) -> tuple[datetime.datetime, str, str] | None:
    """从句子开头读出时间:(什么时候, 重复, 剩下的字)。读不懂返回 None。"""
    found = RELATIVE_HALF.match(text)
    if found:
        minutes = 90 if found.group(1).startswith("一个半") else 30
        return now + datetime.timedelta(minutes=minutes), "none", text[found.end():]
    found = RELATIVE.match(text)
    if found:
        import friday_focus

        unit = "小时" if found.group("u") in ("小时", "钟头") else "分钟"
        half = "半" if found.group(0).rstrip("以之后").endswith("半") and unit == "小时" else ""
        minutes = friday_focus.cn_number(found.group("n") + half + unit)
        if not minutes or minutes > 7 * 24 * 60:
            return None
        return now + datetime.timedelta(minutes=minutes), "none", text[found.end():]
    found = ABSOLUTE.match(text)
    if not found:
        return None
    hour = cn_int(found.group("h"))
    if hour is None or hour > 24:
        return None
    m = found.group("m") or ""
    minute = {"半": 30, "一刻": 15, "三刻": 45, "": 0}.get(m)
    if minute is None:
        minute = cn_int(m.rstrip("分"))
        if minute is None:
            return None
    part, day, rep = found.group("part") or "", found.group("day") or "", found.group("rep") or ""
    if day in ("今晚", "明晚") and not part:
        part = "晚上"
    if day == "明早" and not part:
        part = "早上"
    if part in ("下午", "傍晚", "晚上") and hour < 12:
        hour += 12
    elif part == "中午" and hour < 6:
        hour += 12
    elif part in ("半夜", "凌晨") and hour == 12:
        hour = 0
    if hour == 24:
        hour = 0
    if hour > 23:
        return None
    base = now.replace(second=0, microsecond=0)
    when = base.replace(hour=hour, minute=minute)
    repeat = "none"
    if rep in ("每天", "每日"):
        repeat = "daily"
    elif "工作日" in rep:
        repeat = "weekdays"
    elif rep:
        repeat = "weekly"
        target = _weekday(rep[-1])
        when += datetime.timedelta(days=(target - when.weekday()) % 7)
    if day in ("明天", "明晚", "明早"):
        when += datetime.timedelta(days=1)
    elif day == "后天":
        when += datetime.timedelta(days=2)
    elif day and day[-1] in WEEK and day not in ("今天",):
        target = _weekday(day[-1])
        ahead = (target - when.weekday()) % 7
        if day.startswith("下"):
            ahead = (7 - when.weekday()) + target  # 下周一 = 下一个星期的星期一
        elif ahead == 0 and when <= now:
            ahead = 7
        when += datetime.timedelta(days=ahead)
    elif not day and not rep and when <= now:
        if not part and hour < 12 and when.replace(hour=hour + 12) > now:
            when = when.replace(hour=hour + 12)  # 下午两点说“五点提醒我”= 今天 17:00
        else:
            when += datetime.timedelta(days=1)
    return when, repeat, text[found.end():]


def parse_reminder(text: str, now: datetime.datetime) -> tuple[datetime.datetime, str, str] | None:
    """“十分钟后提醒我喝水”“明天早上八点提醒我开会”“提醒我下午三点打电话给妈妈”“每天晚上十一点叫我睡觉”
    -> (时间, 重复, 要提醒的事)。"""
    sentence = normalize(text)
    found = REMIND.match(sentence)
    if not found or any(w in sentence for w in ("如果", "要是", "的话", "取消", "删掉", "不要提醒", "别提醒")):
        return None
    before, after = found.group("a"), found.group("b")
    if before:
        parsed = parse_time_prefix(before, now)
        if parsed is None or parsed[2] not in ("", "的时候", "再"):
            return None
        when, repeat, _ = parsed
        message = after
    else:
        parsed = parse_time_prefix(after, now)
        if parsed is None:
            return None
        when, repeat, message = parsed
    message = re.sub(r"^(要|去|该|记得|一下|下)+", "", message).strip()
    if not message or len(message) > 60:
        return None
    return when, repeat, message


# ======================== 待办 ========================

TODO_LIST = re.compile(r"^(我的|我还有)?(有)?(什么|哪些|几个)?(待办|待办事项|待办清单|要做的事|没做的事)(清单|列表)?(有哪些|有什么|还有什么)?$"
                       r"|^(我)?还有(什么|哪些)(事)?(没做|要做|没完成)$|^(看看|列出|查看)(我的)?待办$")
TODO_ADD = re.compile(r"^(加|添加|新增|记)(一个|个|一条|条)?待办(事项)?[:：]?(?P<t>.{1,60})$|^待办[:：](?P<t2>.{1,60})$"
                      r"|^把(?P<t3>.{1,40})(加到|加进|放进|记到)待办(清单|事项)?(里)?$")
TODO_DONE = re.compile(r"^(?P<t>.{1,30}?)(已经)?(做完了|完成了|搞定了|弄好了)$|^(完成|划掉|勾掉)(待办)?(?P<t2>.{1,30})$")


# ======================== 天气 ========================

WEATHER = re.compile(
    r"^(?P<place>[一-鿿A-Za-z ]{0,12}?)?(?P<day>今天|明天|后天|现在|今晚|明早|等一下|待会|等下)?(的)?"
    r"(天气(预报)?(怎么样|如何|好吗|好不好|会怎样)?|会不会下雨|会下雨吗|要不要带伞|需要带伞吗|下不下雨|有没有下雨|有雨吗|"
    r"(几度|多少度|热不热|热吗|冷不冷|温度多少|气温多少))$")
NOT_PLACES = ("外面", "这里", "我们这里", "家里", "这边", "今天", "明天", "现在")


def parse_weather(text: str) -> tuple[str, int, bool] | None:
    """(地点, 第几天:0 今天/1 明天/2 后天, 只问下不下雨)。"""
    sentence = normalize(text)
    found = WEATHER.match(sentence)
    if not found:
        return None
    place = (found.group("place") or "").strip()
    day_word = found.group("day") or ""
    for word in ("今天", "明天", "后天", "现在"):  # “槟城明天天气”:地点后面跟着日子
        if place.endswith(word):
            place, day_word = place[: -len(word)], day_word or word
    if place in NOT_PLACES:
        place = ""
    if re.search(r"[你我他她觉得想说看会要的吗呢么这那今明]", place):
        return None  # “你觉得天气怎么样”:不是在问某个地方
    day = {"明天": 1, "明早": 1, "后天": 2}.get(day_word, 0)
    rain_only = any(w in sentence for w in ("雨", "伞"))
    return place, day, rain_only


def describe_weather(place: dict, data: dict, day: int = 0, rain_only: bool = False) -> str:
    """一两句口语:现在怎样、今天几度、几点可能下雨、要不要带伞。"""
    import friday_weather

    current = data.get("current") or {}
    daily = data.get("daily") or {}
    hourly = data.get("hourly") or {}
    days = daily.get("time") or []
    if day >= len(days):
        raise ValueError("没有那一天的天气")
    name = place["name"]
    low, high = daily["temperature_2m_min"][day], daily["temperature_2m_max"][day]
    chance = daily["precipitation_probability_max"][day] or 0
    words = friday_weather.describe_code(daily["weather_code"][day])
    when = ("今天", "明天", "后天")[day]
    date = days[day]
    now_hour = str(current.get("time", ""))[:13] if day == 0 else ""
    rainy = []
    for i, t in enumerate(hourly.get("time") or []):
        if t[:10] == date and (not now_hour or t[:13] >= now_hour) and (hourly["precipitation_probability"][i] or 0) >= 50:
            rainy.append(int(t[11:13]))
    if rainy:
        start, end = rainy[0], rainy[-1] + 1
        rain_text = f"{_clock(start)}到{_clock(end)}可能下雨(最高 {chance}%),出门记得带伞。"
    elif chance >= 30:
        rain_text = f"降雨概率 {chance}%,有机会下一点雨,带把伞比较保险。"
    else:
        rain_text = "应该不会下雨。" if day else ("接下来应该不会下雨。" if now_hour else "应该不会下雨。")
    if rain_only:
        return f"{name}{when}:{rain_text}"
    parts = []
    if day == 0 and current:
        parts.append(f"{name}现在{friday_weather.describe_code(current.get('weather_code'))},"
                     f"{round(current.get('temperature_2m', 0))}°C(体感 {round(current.get('apparent_temperature', 0))}°C)。")
        parts.append(f"今天{words},{round(low)}~{round(high)}°C。")
    else:
        parts.append(f"{name}{when}{words},{round(low)}~{round(high)}°C。")
    parts.append(rain_text)
    return "".join(parts)


def _clock(hour: int) -> str:
    hour %= 24
    if hour == 0:
        return "半夜 12 点"
    if hour < 12:
        return f"早上 {hour} 点" if hour < 9 else f"上午 {hour} 点"
    if hour == 12:
        return "中午 12 点"
    if hour == 18:
        return "傍晚 6 点"
    return f"下午 {hour - 12} 点" if hour < 18 else f"晚上 {hour - 12} 点"


def weather(place_name: str, day: int, rain_only: bool, fetch=None) -> str:
    import friday_weather

    fetch = fetch or friday_weather.fetch_json
    place = friday_weather.find_place(place_name or friday_weather.DEFAULT_PLACE, fetch)
    return describe_weather(place, friday_weather.forecast(place, max(1, day + 1), fetch), day, rain_only)
