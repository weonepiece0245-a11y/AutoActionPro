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
            (re.compile(r"^(你|星期五|AI|ai|Claude|claude)(这个月|本月|这月)?(的)?(钱|花费|费用|用量)?(都)?"
                        r"(花在哪里?了?|花了多少钱?|用了多少钱?|花费|用量|明细|花费明细)$"
                        r"|^(这个月|本月|这月)?(的)?(AI|ai|Claude|claude) ?(花费|费用|用量)(明细|报告)?$"
                        r"|^(这个月|本月)?花费(明细|报告)$"), self._spending),
        ]

    def handle(self, text: str) -> str | None:
        self.silent = False
        if not quick_enabled() or not text or len(text) > 30:
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
