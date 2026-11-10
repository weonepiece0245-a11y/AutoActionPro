"""简单指令不花钱:一句话就能办的事(调音量、切歌、几点了、锁屏、记账……)在电脑上直接认出来照做,不问 Claude。

只认很确定的说法,整句都要对得上;认不出来、或者办不成的,照常交给 Claude。
"""

from __future__ import annotations

import datetime
import os
import re
from typing import Callable

WEEKDAYS = "一二三四五六日"
# 句子前后常带的客气话、称呼,先去掉再认
FILLER_HEAD = re.compile(r"^(星期五|friday|嘿|喂|好的|那|麻烦你?|请你?|帮我|给我|帮忙|你|把|可以|能不能|能)+", re.I)
FILLER_TAIL = re.compile(r"(吧|啊|呀|哦|喔|呢|一下|下|好吗|好不好|谢谢|谢了|可以吗|行吗)+$")
PUNCT = re.compile("[\\s,\uff0c\u3002!\uff01?\uff1f~\uff5e\u3001;\uff1b:\uff1a\u2026]+")  # 不去掉“.”(金额里有小数点)

EXPENSE_CATEGORIES = [
    ("餐饮", "早餐 早饭 午餐 午饭 晚餐 晚饭 宵夜 夜宵 饭 吃 咖啡 奶茶 饮料 水果 零食 面 粥 茶 kopi teh 麦当劳 肯德基 外卖 foodpanda grabfood 饮品 早点"),
    ("交通", "grab 打车 的士 taxi 油 汽油 加油 停车 parking 过路费 tol toll 地铁 mrt lrt 巴士 bus 火车 机票 touchngo tng"),
    ("购物", "衣服 鞋 买 网购 shopee lazada 日用品 超市 商场 包"),
    ("娱乐", "电影 游戏 steam 充值 唱歌 ktv 门票 订阅 netflix spotify"),
    ("账单", "电费 水费 网费 话费 房租 租金 保险 手机费 wifi unifi astro 管理费"),
    ("医疗", "药 看病 医生 诊所 医院 牙 体检"),
]
FORBIDDEN_ITEM = ("多少", "几", "什么", "怎么", "提醒", "音量", "调", "查", "看", "是不是", "吗", "呢")


def clean(text: str) -> str:
    text = PUNCT.sub(" ", str(text or "")).strip().rstrip(".")
    text = FILLER_HEAD.sub("", text.replace(" ", "")).strip()
    return FILLER_TAIL.sub("", text).strip()


def guess_category(item: str) -> str:
    low = item.lower()
    for category, words in EXPENSE_CATEGORIES:
        if any(w in low for w in words.split()):
            return category
    return "其他"


def quick_enabled() -> bool:
    return os.environ.get("FRIDAY_QUICK", "on") != "off"


class QuickCommands:
    """handle(句子) -> 已经办好的回答;认不出来返回 None(交给 Claude)。"""

    def __init__(self, toolbox, clock: Callable[[], datetime.datetime] = datetime.datetime.now,
                 meter=None, budget=None) -> None:
        self.toolbox = toolbox
        self.meter = meter  # 花费统计(“这个月钱花在哪里”)
        self.budget = budget
        self.scenes = None  # 场景模式(friday_scenes.Scenes)
        self.screentime = None  # 电脑使用时间(friday_screentime.ScreenTime)
        self.notes = None  # 速记本(friday_notes.Notes)
        self.dates = None  # 生日和纪念日(friday_dates.Dates)
        self.virus = None  # 病毒扫描:(开始(kind), 状态())
        self.on_say = None  # 场景里“说”的那一步:马上读出来
        self.silent = False  # 这次的回答已经读过了(场景模式),不用再读
        self.clock = clock
        self.rules: list[tuple[re.Pattern, Callable]] = [
            (re.compile(r"^(现在)?(几点了?|几点钟|什么时间了?|现在时间)$"), self._time),
            (re.compile(r"^(今天)?(几号|几月几号|星期几|礼拜几|周几|什么日子|日期)$"), self._date),
            (re.compile(r"^(音量|声音)(调|开|设)?(到|成|为)?(\d{1,3})%?$"), self._volume_to),
            (re.compile(r"^(音量|声音)?(调|开)?(大|高)(声)?(一点|点|些)?$|^(大声|大声点|大声一点)$"), self._louder),
            (re.compile(r"^(音量|声音)?(调|开)?(小|低)(声)?(一点|点|些)?$|^(小声|小声点|小声一点)$"), self._softer),
            (re.compile(r"^(静音|取消静音|关掉声音|打开声音)$"), self._mute),
            (re.compile(r"^(下一首|下一首歌|切歌|换一首|换首歌)$"), self._media("next_track", "已切到下一首。")),
            (re.compile(r"^(上一首|上一首歌)$"), self._media("previous_track", "已切到上一首。")),
            (re.compile(r"^(暂停|暂停播放|继续播放|暂停音乐|播放音乐|继续放歌)$"), self._media("play_pause", "好了。")),
            (re.compile(r"^(锁屏|锁定屏幕|锁电脑|锁住电脑)$"), self._lock),
            (re.compile(r"^(开)?(游戏模式)(开启|打开)?$"), self._game_mode),
            (re.compile(r"^(删掉|删除|撤销|取消)(刚才|刚刚|上一|最后一)(那|一)?(笔|条)(账|记账)?$"), self._undo_expense),
            (re.compile(r"^(记账|记一笔|记)?(?P<item>[一-鿿A-Za-z][一-鿿A-Za-z ]{0,14}?)(花了|用了)?"
                        r"(rm|RM|Rm)?(?P<amount>\d{1,6}(\.\d{1,2})?)(块钱|块|元|令吉|马币|rm|ringgit)$"), self._expense),
            (re.compile(r"^打开(?P<app>[一-鿿A-Za-z0-9 ]{1,20})$"), self._open_app),
            (re.compile(r"^(最近|接下来|这个月|下个月)?(有)?(谁|什么人)?(过)?(生日|的生日|纪念日|重要的日子)(吗|呢)?$"), self._dates),
            (re.compile(r"^(我)?(?P<when>今天|昨天|这周|这个星期|本周)?(用了?多久电脑|电脑用了?多久|玩了多久(游戏)?|打了多久游戏|"
                        r"游戏玩了多久|使用时间|屏幕时间|用电脑多久了?)$"), self._screen_time),
            (re.compile(r"^(你|星期五|AI|ai|Claude|claude)(这个月|本月|这月)?(的)?(钱|花费|费用|用量)?(都)?"
                        r"(花在哪里?了?|花了多少钱?|用了多少钱?|花费|用量|明细|花费明细)$"
                        r"|^(这个月|本月|这月)?(的)?(AI|ai|Claude|claude) ?(花费|费用|用量)(明细|报告)?$"
                        r"|^(这个月|本月)?花费(明细|报告)$"), self._spending),
            (re.compile(r"^(做|来|开始|开|跑)?(个|一个|一次)?(电脑)?(?:"
                        r"(快速|全面|完整|全盘|深度)(的)?(病毒)?(扫描|扫|查杀|杀毒)(一下|一遍|一次)?(电脑|全盘)?(的)?(病毒|毒)?"
                        r"|(病毒)?(扫描|扫|查杀|查|杀)(一下|一遍|一次|下)?(电脑|全盘)?(的)?(病毒|毒)"
                        r"|病毒扫描|杀毒)$"), self._virus),
            (re.compile(r"^(病毒)?(扫描|扫毒|杀毒)(的)?(结果|进度|状态|情况)(怎么样|如何)?$"
                        r"|^(病毒扫描|扫描|扫毒|杀毒)(好了没有?|完了没有?|做完了没有?|扫完了没有?|怎么样了?)$"
                        r"|^(电脑|我的电脑)?(有没有|有)(中)?病毒$|^上次(什么时候)?(扫描|扫毒|杀毒)(是)?(什么时候)?$"), self._virus_status),
        ]

    NOTE_ADD = re.compile(r"^\s*(星期五[,,\s]*)?(帮我)?(记一下|记下来|记下|备忘一下|备忘|速记)\s*[::,,]?\s*(?P<text>.+)$", re.S)
    NOTE_FIND = re.compile(r"^\s*(帮我)?(找|查|搜)(一下)?(我的)?笔记\s*[::,,]?\s*(?P<q>.+?)\s*$|"
                           r"^\s*笔记里(有没有|找一下|找)\s*(?P<q2>.+?)(吗|的)?\s*[??]?$")
    NOTE_RECENT = re.compile(r"^\s*(看一?下|看看|打开)?(我的|最近的?)?笔记(本)?\s*[??!!。]?$")
    NOTE_DELETE = re.compile(r"^\s*(删掉|删除|撤销)(刚才|刚刚|上一|最后一)(那|一)?条笔记\s*[。!!]?$")

    def _notes(self, text: str) -> str | None:
        notes = self.notes
        if notes is None:
            return None
        if self.NOTE_DELETE.match(text):
            return notes.delete("last")
        found = self.NOTE_FIND.match(text)
        if found:
            query = found.group("q") or found.group("q2") or ""
            return notes.describe(notes.search(query), f"笔记里没有“{query}”")
        if self.NOTE_RECENT.match(text):
            return notes.describe(notes.search(limit=10), "笔记本还是空的。说“记一下:……”就能记。")
        found = self.NOTE_ADD.match(text)
        if found and not found.group("text").startswith(("住", "账")):  # “记住”是长期记忆,“记账”是记账
            return notes.add(found.group("text"))
        return None

    def handle(self, text: str) -> str | None:
        self.silent = False
        if not quick_enabled() or not text:
            return None
        if self.notes is not None and len(text) <= 2000:
            try:
                reply = self._notes(text)
            except ValueError as e:
                reply = str(e)
            if reply:
                return reply
        if len(text) > 30:
            return None
        sentence = clean(text)
        if not sentence:
            return None
        if self.scenes is not None:
            scene = self.scenes.find(text) or self.scenes.find(sentence)
            if scene is not None:
                return self._scene(scene)
        for pattern, action in self.rules:
            found = pattern.match(sentence)
            if found:
                try:
                    return action(found)
                except Exception:
                    return None  # 办不成:交给 Claude 处理
        return None

    # ---------- 时间 ----------
    def _time(self, found):
        now = self.clock()
        return f"现在是 {now:%H:%M}。"

    def _date(self, found):
        now = self.clock()
        return f"今天是 {now.year} 年 {now.month} 月 {now.day} 日,星期{WEEKDAYS[now.weekday()]}。"

    # ---------- 电脑 ----------
    def _computer(self):
        computer = getattr(self.toolbox, "computer", None)
        if computer is None or not computer.available:
            raise RuntimeError("不能控制电脑")
        return computer

    def _volume_to(self, found):
        level = int(found.group(4))
        if level > 100:
            return None
        computer = self._computer()
        computer.do("volume_down", "100")  # 先调到 0,再调到想要的大小(每按一下 2%)
        if level:
            computer.do("volume_up", str(level))
        return f"音量调到大约 {level - level % 2}% 了。"

    def _louder(self, found):
        self._computer().do("volume_up", "10")
        return "音量调大了一点。"

    def _softer(self, found):
        self._computer().do("volume_down", "10")
        return "音量调小了一点。"

    def _mute(self, found):
        self._computer().do("mute")
        return "已切换静音。"

    def _media(self, action: str, reply: str):
        def run(found):
            self._computer().do(action)
            return reply

        return run

    def _lock(self, found):
        self._computer().do("lock_screen")
        return "已锁屏。"

    def _game_mode(self, found):
        result = self._computer().do("game_mode")
        if "还没在齿轮设定里选好" in result:
            return None  # 没选过要关哪些:交给 Claude 问你
        return result + "。"

    def _open_app(self, found):
        name = found.group("app").strip()
        if any(w in name for w in ("文件", "网页", "网站", "链接", "http", "www", "设定", "设置", "窗口")):
            return None
        result = self._computer().do("open_app", name)
        return result + "。" if result.startswith("已打开") else None  # 没找到:交给 Claude(它会试游戏、网页)

    # ---------- 生日 ----------
    def _dates(self, found):
        if self.dates is None or not (found.group(1) or found.group(3)):
            return None  # 只说“生日”太含糊
        return self.dates.describe_upcoming(30)

    # ---------- 病毒扫描 ----------
    def _virus(self, found):
        if self.virus is None:
            return None
        full = any(w in found.group(0) for w in ("全面", "完整", "全盘", "深度"))
        return self.virus[0]("full" if full else "quick")

    def _virus_status(self, found):
        if self.virus is None:
            return None
        return self.virus[1]()

    # ---------- 使用时间 ----------
    def _screen_time(self, found):
        if self.screentime is None:
            return None
        when = found.group("when") or "今天"
        period = {"昨天": "yesterday", "这周": "week", "这个星期": "week", "本周": "week"}.get(when, "today")
        self.screentime.save()
        return self.screentime.report(period)

    # ---------- 场景 ----------
    def _scene(self, scene: dict) -> str:
        import friday_scenes

        said, done = friday_scenes.run(scene, getattr(self.toolbox, "computer", None), on_say=self.on_say)
        self.silent = self.on_say is not None
        summary = f"「{scene['name']}」场景:" + (";".join(done) if done else "做好了")
        return (said + "\n" + summary) if said else summary

    # ---------- 花费 ----------
    def _spending(self, found):
        if self.meter is None:
            return None
        import friday_alerts

        return friday_alerts.month_report(self.meter, self.clock().date(), self.budget)

    # ---------- 记账 ----------
    def _expense(self, found):
        ledger = getattr(self.toolbox, "ledger", None)
        item = re.sub(r"^(今天的?|刚才|刚刚)", "", found.group("item").strip())
        if ledger is None or not item or any(w in item for w in FORBIDDEN_ITEM):
            return None
        if item.startswith(("昨天", "前天", "上周", "上个", "我", "他", "她", "我们")):
            return None  # 要算日期、或者不像一笔账:交给 Claude
        return ledger.add(item, found.group("amount"), guess_category(item))

    def _undo_expense(self, found):
        ledger = getattr(self.toolbox, "ledger", None)
        if ledger is None:
            return None
        return ledger.delete("last")
