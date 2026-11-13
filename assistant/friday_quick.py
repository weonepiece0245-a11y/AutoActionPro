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
        self.bills = None  # 账单(friday_bills.Bills)
        self.focus = None  # 专注模式(friday_focus.Focus)
        self.brief = None  # () -> 早安简报
        self.clips = None  # 剪贴板历史(friday_clipboard.ClipHistory)
        self.habits = None  # 习惯打卡(friday_habits.Habits)
        self.speed = None  # () -> 开始测网速,马上返回
        self.netwatch = None  # 断网记录(friday_net.NetWatch)
        self.fx = None  # (friday_fx.Rates, friday_fx.FxAlerts)
        self.news = None  # 新闻(friday_news.News)
        self.steam = None  # Steam(friday_steam.Steam)
        self.nvidia = False  # 有没有 NVIDIA 显卡
        self.folder_backup = None  # (friday_folderbackup.FolderBackup, 马上备份() -> 话)
        self.interpret_start = None  # (语言代码) -> 话
        self.reader = None  # (friday_reader.Reader, 开始读(标题, 段落) -> 话)
        self.spotify = None  # friday_spotify.Spotify
        self.dota = None  # friday_dota.Dota
        self.apex = None  # friday_apex.Apex
        self.gameboost = None  # friday_gameboost.GameBoost
        self.known_games = None  # friday_games.KnownGames
        self.temps = None  # friday_temps.Temps
        self.gamereport = None  # friday_gamereport.GameReport
        self.wishlist = None  # friday_wishlist.Wishlist
        self.dota_advice = None  # friday_dota_advice.DotaAdvice
        self.selfcheck = None  # friday_selfcheck.SelfCheck
        self.party = None  # (开始开黑, 不玩了):两个函数
        self.lessons = None  # 被纠正就记住(friday_lessons.Lessons)
        self.vocab = None  # 听错改正词库(friday_voicefix.Vocab)
        self.background = None  # 后台长任务(friday_background.BackgroundJobs)
        self.extra: list = []  # 其他功能自己认的说法:有 handle(句子) -> 话 或 None 的东西
        self.env_file = None  # 改设定时存到哪个 .env
        self.sleep_timer = None  # friday_sleeptimer.SleepTimer
        self.diary = None  # friday_diary.Diary
        self.shopping = None  # friday_shopping.Shopping
        self.home = None  # friday_tapo.TapoHome
        self.floatorb = None  # (要不要显示) -> 话:桌面小光球
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
            (re.compile(r"^打开第(?P<n>\d{1,2}|[一二三四五六七八九十]{1,3})个(文件)?$"), self._open_found),
            (re.compile(r"^(找|搜|搜索|查找|帮我找)(一下)?(文件|档案)(?P<q>[^\s]{1,20})$"), self._find_file),
            (re.compile(r"(?i)^(早安|早上好|早啊|早晨|goodmorning|good morning|今日简报|今天的?简报|简报|"
                        r"今天有什么安排|今天有什么事|今天要做什么|今天的安排)$"), self._brief),
            (re.compile(r"^(我要|我想|开始|开|开启|进入|来个?|来一个)?(专注|番茄钟|番茄|专心)(模式)?"
                        r"(?P<m>\d{1,3}|[零一二两三四五六七八九十半]{1,4}个?)?(?P<u>分钟|分|个?小时|个?钟头)?$"), self._focus_start),
            (re.compile(r"^(停止|结束|取消|退出|关掉|关闭|停)(专注|番茄钟|番茄)(模式)?$"), self._focus_stop),
            (re.compile(r"^(专注|番茄钟|番茄)?(还剩|还有)(多久|多少时间|多少分钟|几分钟)$"), self._focus_status),
            (re.compile(r"^(?P<when>今天|这周|本周|这个星期)?(专注了?(多久|多少时间|统计)|(完成了?|做了)几个番茄钟?|番茄钟?统计)$"),
             self._focus_report),
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

    NOTE_ADD = re.compile(r"^\s*(星期五[,，\s]*)?(帮我)?(记一下|记下来|记下|备忘一下|备忘|速记)\s*[:：,，]?\s*(?P<text>.+)$", re.S)
    NOTE_FIND = re.compile(r"^\s*(帮我)?(找|查|搜)(一下)?(我的)?笔记\s*[:：,，]?\s*(?P<q>.+?)\s*$|"
                           r"^\s*笔记里(有没有|找一下|找)\s*(?P<q2>.+?)(吗|的)?\s*[?？]?$")
    NOTE_RECENT = re.compile(r"^\s*(看一?下|看看|打开)?(我的|最近的?)?笔记(本)?\s*[?？!！。]?$")
    NOTE_DELETE = re.compile(r"^\s*(删掉|删除|撤销)(刚才|刚刚|上一|最后一)(那|一)?条笔记\s*[。!！]?$")

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
        if self.shopping is not None and len(text) <= 120:
            found = self.SHOP_ADD.match(re.sub(r"\s*[:\uff1a]\s*", "", text.strip().rstrip("。!\uff01")))
            if found:  # 用原话(带顿号、逗号)分开每一样东西
                return self.shopping.add(found.group("t") or found.group("t2"))
        if self.diary is not None:
            found = self.DIARY_WRITE.match(text)
            if found:
                try:
                    return self.diary.write(found.group("t"))
                except ValueError as e:
                    return str(e)
        if self.reader is not None:
            try:
                reply = self._reader_url(text)
            except ValueError as e:
                reply = str(e)
            if reply:
                return reply
        for helper in (self.lessons, self.vocab, self.background, *self.extra):
            if helper is not None and len(text) <= (400 if helper is self.background else 80):
                try:
                    reply = helper.handle(text)
                except Exception:
                    reply = None
                if reply:
                    return reply
        if len(text) <= 80:
            try:
                reply = self._local_ask(text)
            except ValueError as e:
                reply = str(e)
            except Exception:
                reply = None
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
        if self.bills is not None:
            try:
                reply = self._bills(sentence)
            except ValueError:
                reply = None
            if reply:
                return reply
        for check in (self._reader_control, self._interpret, self._clipboard, self._shopping_done, self._habits, self._network, self._money,
                      self._timer, self._games, self._diary, self._shopping, self._home, self._apps):
            try:
                reply = check(sentence)
            except ValueError as e:
                reply = str(e)
            except Exception:
                reply = None
            if reply:
                return reply
        for pattern, action in self.rules:
            found = pattern.match(sentence)
            if found:
                try:
                    return action(found)
                except Exception:
                    return None  # 办不成:交给 Claude 处理
        return None

    # ---------- 天气、待办、提醒(friday_localask) ----------
    def _local_ask(self, text: str) -> str | None:
        import friday_localask as local

        toolbox = self.toolbox
        reminders = getattr(toolbox, "reminders", None)
        todos = getattr(toolbox, "todos", None)
        if reminders is not None:
            parsed = local.parse_reminder(text, self.clock())
            if parsed is not None:
                return self._add_reminder(*parsed)
        sentence = local.normalize(text)
        if len(sentence) > 30:
            return None
        if reminders is not None:
            if local.LIST.match(sentence):
                import friday_daily

                items = reminders.upcoming()
                if not items:
                    return "现在没有提醒。说“十分钟后提醒我……”就能设。"
                return f"现在有 {len(items)} 个提醒:\n" + "\n".join(friday_daily.describe_reminder(i) for i in items[:15])
            found = local.CANCEL.match(sentence)
            if found:
                key = (found.group("k") or found.group("k2") or "").strip()
                if key and key not in ("所有", "全部", "这个", "那个"):
                    reply, failed = toolbox._reminder({"action": "cancel", "key": key}, lambda status: None)
                    return None if failed else reply
        google = getattr(toolbox, "google", None)
        if google is not None:  # 连了 Google:待办在 Google Tasks(手机上也看得到)
            if local.TODO_LIST.match(sentence):
                items = google.task_items()
                if not items:
                    return "待办清单是空的,都做完了。"
                return f"还有 {len(items)} 件待办:\n" + "\n".join(
                    f"□ {i['text']}" + (f"(截止 {i['due']})" if i.get("due") else "") for i in items[:20])
            found = local.TODO_ADD.match(sentence)
            if found:
                title = found.group("t") or found.group("t2") or found.group("t3")
                google.tasks({"action": "add", "title": title})
                return f"加到待办了:{title}(手机上的 Google Tasks 也看得到)。"
            found = local.TODO_DONE.match(sentence)
            if found:
                key = (found.group("t") or found.group("t2") or "").strip().removeprefix("待办")
                matches = [i for i in google.task_items() if key and (key in i["text"] or i["text"] in key)]
                if len(matches) == 1:
                    google.tasks({"action": "done", "key": matches[0]["text"]})
                    return f"好,“{matches[0]['text']}”打勾了。"
                return None
        elif todos is not None:
            if local.TODO_LIST.match(sentence):
                import friday_daily

                items = todos.pending()
                if not items:
                    return "待办清单是空的,都做完了。"
                return f"还有 {len(items)} 件待办:\n" + "\n".join(friday_daily.describe_todo(i) for i in items[:20])
            found = local.TODO_ADD.match(sentence)
            if found:
                item = todos.add(found.group("t") or found.group("t2") or found.group("t3"))
                return f"加到待办了:{item['text']}。"
            found = local.TODO_DONE.match(sentence)
            if found:
                key = (found.group("t") or found.group("t2") or "").strip()
                if key.startswith("待办"):
                    key = key[2:]
                matches = [i for i in todos.pending() if key and (key in i["text"] or i["text"] in key)]
                if len(matches) == 1:
                    done = todos.complete(matches[0]["text"])
                    left = len(todos.pending())
                    if done:
                        return f"好,“{done[0]['text']}”打勾了。" + (f"还有 {left} 件待办。" if left else "待办全部做完了!")
        weather = local.parse_weather(text)
        if weather is not None:
            place, day, rain_only = weather
            try:
                return local.weather(place, day, rain_only, getattr(toolbox, "weather_fetch", None))
            except ValueError:
                return None  # 地名找不到之类:交给 Claude
        return None

    def _add_reminder(self, when, repeat: str, message: str) -> str:
        import friday_daily

        item = self.toolbox.reminders.add(when.strftime(friday_daily.TIME_FORMAT), message, "notify", repeat)
        when = friday_daily.parse_time(item["time"])
        today = self.clock().date()
        day = {0: "今天", 1: "明天", 2: "后天"}.get((when.date() - today).days,
                                                f"{when.month}月{when.day}日(星期{WEEKDAYS[when.weekday()]})")
        label = {"daily": "每天 ", "weekdays": "每个工作日 ", "weekly": f"每周{WEEKDAYS[when.weekday()]} "}.get(repeat, "")
        when_text = f"{label}{when:%H:%M}" + (f"(第一次是{day})" if label else "") if label else f"{day} {when:%H:%M}"
        try:
            calendar = self.toolbox._reminder_to_calendar(item)
        except Exception:
            calendar = ""
        tail = "手机上也会响。" if "也放进了 Google 日历" in calendar else ""
        return f"好,{when_text} 提醒你:{message}。{tail}"

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
            spotify = self.spotify
            if spotify is not None:  # Spotify 在放:直接叫 Spotify,不按媒体键(浏览器的 YouTube 抢不走)
                try:
                    done = spotify.media(action)
                except Exception:
                    done = None
                if done:
                    return done
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
        if result.startswith("已打开") or "本来就在运行" in result:
            return result if result.endswith(("。", ")", ")")) else result + "。"
        return None  # 没找到、或者没开起来:交给 Claude(它会试游戏、网页,或者问你能不能用开始菜单搜索)

    # ---------- 生日 ----------
    def _dates(self, found):
        if self.dates is None or not (found.group(1) or found.group(3)):
            return None  # 只说“生日”太含糊
        return self.dates.describe_upcoming(30)

    # ---------- 账单 ----------
    BILL_PAID = re.compile(r"^(?P<a>[一-鿿A-Za-z ]{1,10}?)(已经)?(付|交|缴|还|转)(了|好了|完了|掉了)(钱)?(rm|RM)?"
                           r"(?P<x>\d{1,6}(\.\d{1,2})?)?(块钱|块|元|令吉|马币)?$"
                           r"|^(已经)?(付|交|缴)(了|好了)(?P<b>[一-鿿A-Za-z ]{1,10}?)(rm|RM)?(?P<y>\d{1,6}(\.\d{1,2})?)?(块钱|块|元|令吉|马币)?$")

    def _bills(self, sentence: str) -> str | None:
        found = self.BILL_PAID.match(sentence)
        if found:
            name = (found.group("a") or found.group("b") or "").strip()
            bill = self.bills.find(name) if name else None
            if bill is None:
                return None  # 不是记着的账单:照常处理(可能是普通记账)
            text, bill, amount = self.bills.mark_paid(bill["name"], found.group("x") or found.group("y"))
            ledger = getattr(self.toolbox, "ledger", None)
            if amount and ledger is not None:
                ledger.add(bill["name"], amount, "账单")
            return text
        if "账单" in sentence and len(sentence) <= 14 and not re.search(r"\d|删|记|加|每|改|设", sentence):
            return self.bills.describe()
        return None

    # ---------- 读给我听 ----------
    URL_RE = re.compile(r"https?://[^\s<>\"'，。]+")
    READ_WORDS = re.compile(r"(读|念|朗读)")

    def _reader_url(self, text: str) -> str | None:
        found = self.URL_RE.search(text)
        if not found or not self.READ_WORDS.search(text.replace(found.group(0), "")):
            return None
        import friday_reader

        reader, start = self.reader
        title, paras = friday_reader.load_url(found.group(0).rstrip(").,;"))
        return start(title, paras)

    READ_CLIP = re.compile(r"^(把)?(我)?(刚才|刚刚)?(复制的|剪贴板里?的|选中的|拷贝的)(文字|内容|东西|那段)?(读|念|朗读)(给我听|一下|出来|一遍)?$"
                           r"|^(读|念|朗读)(一下)?(我)?(刚才)?(复制的|剪贴板里?的|选中的)(文字|内容|东西|那段)?(给我听)?$")
    READ_FOUND = re.compile(r"^(读|念|朗读)(一下)?第(?P<n>\d{1,2}|[一二三四五六七八九十]{1,3})个文件(给我听)?$")
    READ_PAUSE = re.compile(r"^(暂停|停一下|等一下|先停|暂停朗读|暂停一下)$")
    READ_RESUME = re.compile(r"^(继续|继续读|接着读|继续念|接着念|继续朗读)$")
    READ_STOP = re.compile(r"^(停止朗读|别读了|不要读了|不用读了|停止读|不读了|别念了|关掉朗读|停止)$")
    READ_NEXT = re.compile(r"^(跳过|跳过这段|下一段|跳到下一段)$")
    READ_BACK = re.compile(r"^(上一段|回到上一段|重读这段|再读一遍这段|这段再读一遍)$")
    READ_RESTART = re.compile(r"^(从头再读|从头读|重新读|从头开始读)$")
    READ_SPEED = re.compile(r"^(读|念)?(快|慢)(一点|点|些)$")
    READ_STATUS = re.compile(r"^(读到哪里了|读到哪了|还有多少|还剩多少)$")

    def _reader_control(self, sentence: str) -> str | None:
        if self.reader is None:
            return None
        reader, start = self.reader
        found = self.READ_FOUND.match(sentence)
        if found and getattr(self.toolbox, "found_files", None):
            import friday_focus
            import friday_reader

            n = found.group("n")
            number = int(n) if n.isdigit() else (friday_focus.cn_number(n) or 0)
            if not 1 <= number <= len(self.toolbox.found_files):
                return "没有这个编号的文件。"
            title, paras = friday_reader.load_file(self.toolbox.found_files[number - 1]["path"])
            return start(title, paras)
        if self.READ_CLIP.match(sentence):
            import friday_desktop
            import friday_reader

            text = friday_desktop.read_clipboard()
            if not text or not text.strip():
                return "剪贴板里没有文字。先选中文字按 Ctrl+C,再叫我读。"
            return start("复制的文字", friday_reader.paragraphs(text))
        if not reader.active and not (reader.parts and self.READ_RESTART.match(sentence)):
            return None
        if self.READ_PAUSE.match(sentence):
            return reader.pause()
        if self.READ_RESUME.match(sentence):
            return reader.resume()
        if self.READ_STOP.match(sentence):
            return reader.stop()
        if self.READ_NEXT.match(sentence):
            return reader.move(1)
        if self.READ_BACK.match(sentence):
            return reader.move(-1 if sentence.startswith(("上一段", "回到")) else 0)
        if self.READ_RESTART.match(sentence):
            return reader.restart()
        found = self.READ_SPEED.match(sentence)
        if found:
            return reader.speed(found.group(2) == "快")
        if self.READ_STATUS.match(sentence):
            return reader.status()
        return None

    # ---------- 口译 ----------
    def _interpret(self, sentence: str) -> str | None:
        if self.interpret_start is None:
            return None
        import friday_interpret

        found = friday_interpret.START_RE.match(sentence)
        if not found:
            return None
        word = found.group("lang") or found.group("lang2")
        return self.interpret_start(friday_interpret.language_of(word))

    # ---------- 日记 ----------
    DIARY_WRITE = re.compile(r"^\s*(帮我)?(写|记)?(一下)?日记\s*[:：,，]\s*(?P<t>.{2,})$", re.S)
    DIARY_SUMMARY = re.compile(r"^(今天|今日)?(的)?(总结|总结一下|小结|回顾)$|^(帮我)?(总结|回顾)(一下)?今天$")
    DIARY_READ = re.compile(r"^(看|读|念)?(一下)?(?P<d>今天|昨天|前天|大前天|上上?个?(周|星期|礼拜)[一二三四五六日天七]|(这|本)?(周|星期|礼拜)[一二三四五六日天七]"
                            r"|\d{4}-\d{1,2}-\d{1,2}|(\d{4}年)?\d{1,2}月\d{1,2}[日号]?)的?日记$")
    DIARY_FIND = re.compile(r"^(在)?日记(里)?(找|搜)(一下)?(?P<q>.{1,20})$")

    def _diary(self, sentence: str) -> str | None:
        diary = self.diary
        if diary is None:
            return None
        if self.DIARY_SUMMARY.match(sentence):
            return diary.summary()
        found = self.DIARY_READ.match(sentence)
        if found:
            import friday_diary

            day = friday_diary.parse_day(found.group("d"), self.clock().date())
            return diary.read(day) if day else None
        found = self.DIARY_FIND.match(sentence)
        if found:
            return diary.search(found.group("q"))
        return None

    # ---------- 购物清单 ----------
    SHOP_ADD = re.compile(r"^(我)?(要买|还要买|记得买|帮我记(一下)?要买|购物清单(加上?|记上?)|加到购物清单|买东西)"
                          r"(?!什么|啥|哪些|多少|吗|的东西|几样)(?P<t>.{1,60})$"
                          r"|^(?P<t2>.{1,40})(加到|加进|放进|记到)购物清单(里)?$")
    SHOP_LIST = re.compile(r"^(我)?(还)?(要|需要)?买(些)?什么(东西)?$|^(看一?下)?(我的)?(购物清单|购物单|要买的东西)$")
    SHOP_DONE = re.compile(r"^(?P<t>[^\d]{1,12}?)(已经)?(买了|买好了|买到了)(rm|RM)?(?P<x>\d{1,5}(\.\d{1,2})?)?(块钱|块|元|令吉)?$")
    SHOP_REMOVE = re.compile(r"^(购物清单里?)?(不用|不要|别)买(?P<t>.{1,20})了?$")
    SHOP_CLEAR = re.compile(r"^(清空|清掉)购物清单$")

    def _shopping(self, sentence: str) -> str | None:
        shop = self.shopping
        if shop is None:
            return None
        if self.SHOP_CLEAR.match(sentence):
            return shop.clear()
        if self.SHOP_LIST.match(sentence):
            return shop.describe()
        found = self.SHOP_REMOVE.match(sentence)
        if found:
            return shop.remove(found.group("t"))
        found = self.SHOP_ADD.match(sentence)
        if found:
            return shop.add(found.group("t") or found.group("t2"))
        return None

    def _shopping_done(self, sentence: str) -> str | None:
        """“牛奶买了”“牛奶买了 8 块”:划掉;带价钱就顺便记账。清单上没有就不管(可能是普通记账)。"""
        shop = self.shopping
        found = self.SHOP_DONE.match(sentence) if shop is not None else None
        if not found:
            return None
        result = shop.done(found.group("t").strip())
        if result is None:
            return None
        text, item = result
        ledger = getattr(self.toolbox, "ledger", None)
        if found.group("x") and ledger is not None:
            category = guess_category(item["text"])
            ledger.add(item["text"], found.group("x"), "购物" if category == "其他" else category)
            text += f"也记了一笔账 RM {float(found.group('x')):.2f}。"
        return text

    # ---------- 智能家居 ----------
    HOME_ON = re.compile(r"^(把)?(?P<n>.{1,14}?)(打开|开起来|开一下|开了)$|^(打开|开|开一下)(?P<n2>.{1,14})$")
    HOME_OFF = re.compile(r"^(把)?(?P<n>.{1,14}?)(关掉|关了|关一下|关上|关闭)$|^(关掉|关闭|关|关一下)(?P<n2>.{1,14})$")
    HOME_LIGHTER = re.compile(r"^(把)?(?P<n>.{0,10}?)(调亮|调暗|亮一点|暗一点|调亮一点|调暗一点|亮一些|暗一些|亮点|暗点)$")
    HOME_LEVEL = re.compile(r"^(把)?(?P<n>.{0,10}?)(的)?亮度(调到|调成|设为|设成|调|到)?(?P<v>\d{1,3})%?$")
    HOME_WARM = re.compile(r"^(把)?(?P<n>.{0,10}?)(换成|调成|变成|调)?(?P<w>暖光|暖色|暖一点|黄光|白光|冷光|冷一点|白一点)$")
    HOME_COLOR = re.compile(r"^(把)?(?P<n>.{0,10}?)(换成|调成|变成)(?P<c>[红橙黄绿青蓝紫粉白])色(的)?$")
    HOME_STATUS = re.compile(r"^(家里)?(有)?(哪些|什么)?(灯|设备|插座)(还)?(开着|有开着|状态)(吗|没有)?$|^(智能家居|家里的设备|tapo|Tapo)(状态|设备|有哪些)?$")

    def _home(self, sentence: str) -> str | None:
        home = self.home
        if home is None:
            return None
        words = lambda found: (found.group("n") or "").replace("的", "")  # noqa: E731
        if self.HOME_STATUS.match(sentence):
            return home.describe()
        for pattern, on in ((self.HOME_ON, True), (self.HOME_OFF, False)):
            found = pattern.match(sentence)
            if found:
                target = (found.group("n") or found.group("n2") or "").replace("的", "")
                return home.switch(target, on)  # 没有这个设备:返回 None,交给别的规则(例如打开程序)
        found = self.HOME_LEVEL.match(sentence)
        if found:
            return home.brightness(words(found), value=int(found.group("v")))
        found = self.HOME_LIGHTER.match(sentence)
        if found:
            return home.brightness(words(found), step=25 if "亮" in sentence[len(found.group("n") or ""):] else -25)
        found = self.HOME_COLOR.match(sentence)
        if found:
            return home.color(words(found), found.group("c"))
        found = self.HOME_WARM.match(sentence)
        if found:
            return home.warmth(words(found), warm=found.group("w") in ("暖光", "暖色", "暖一点", "黄光"))
        return None

    # ---------- 睡前定时 ----------
    NUM = r"(?P<n>\d{1,3}|[零一二两三四五六七八九十半]{1,4}个?)(?P<u>分钟|分|个?小时|个?钟头)"
    TIMER_MUSIC = re.compile(r"^" + NUM + r"(后|以后|之后)(就)?(停|停止|关掉|关|暂停)(掉)?(音乐|歌|播放|放歌)?$"
                             r"|^" + NUM.replace("?P<n>", "?P<n2>").replace("?P<u>", "?P<u2>") + r"(后|以后|之后)(就)?不(要)?(放|听)了$")
    TIMER_SONG = re.compile(r"^(听完|放完|播完)(这首|这一首|这首歌)(就|以后|之后)?(停|停止|暂停|睡觉|关掉)?$")
    TIMER_CANCEL = re.compile(r"^(取消|关掉|停止|不要)(睡眠|睡前)?定时(器)?$|^不要停(音乐|歌)?了$")
    TIMER_STATUS = re.compile(r"^(睡眠|睡前)?定时(还剩|还有)(多久|多少时间)$|^(还有|还剩)多久(停音乐|停歌|关机|睡眠)$")

    def _timer(self, sentence: str) -> str | None:
        timer = self.sleep_timer
        if timer is None:
            return None
        found = self.TIMER_MUSIC.match(sentence)
        if found:
            import friday_focus

            n, u = found.group("n") or found.group("n2"), found.group("u") or found.group("u2")
            minutes = friday_focus.cn_number(n + u)
            return timer.set("music", minutes) if minutes else None
        if self.TIMER_SONG.match(sentence):
            return timer.set("song")
        if self.TIMER_CANCEL.match(sentence):
            return timer.cancel()
        if self.TIMER_STATUS.match(sentence):
            return timer.status()
        return None

    # ---------- 游戏战绩 ----------
    DOTA = r"(dota|刀塔|dota2|dota 2)"
    DOTA_LAST = re.compile(r"^" + DOTA + r"?(刚才|刚刚|上一|最后一)(那|一)?(局|把|场)(dota|刀塔)?(怎么样|打得怎么样|战绩|如何|数据)?$", re.I)
    DOTA_RECENT = re.compile(r"^" + DOTA + r"?(最近|近)(\d{1,2}|[一二三四五六七八九十]{1,3}|几)?(局|把|场)(dota|刀塔)?"
                             r"(赢了几局|输赢|战绩|怎么样|胜率|赢几局)$", re.I)
    DOTA_HEROES = re.compile(r"^" + DOTA + r"?(我)?(哪个|什么)英雄(胜率最高|最强|玩得最好|赢最多)$|^" + DOTA + r"英雄(胜率|统计)$", re.I)
    DOTA_RANK = re.compile(r"^(我的?)?" + DOTA + r"(的)?(段位|天梯|排名|分段)(是什么|多少)?$", re.I)
    APEX_MAP = re.compile(r"^(现在)?(apex)?(现在)?(是|在|玩)?(什么|哪张|哪个)(地图|图)$|^(apex)?(下一张|下张)(图|地图)(几点|什么时候)(换|是什么)?$"
                          r"|^(apex)?(地图|图)(轮换|什么时候换)$", re.I)
    APEX_RANK = re.compile(r"^(我的?)?apex(的)?(排位|段位|分数|等级|战绩|排位分)(多少|是什么|怎么样)?(分)?$", re.I)

    GAME_CHECK = re.compile(r"^(游戏体检|游戏检查|检查(一下)?(电脑的?)?游戏(设定|设置)|打游戏前(帮我)?检查(一下)?(电脑)?"
                            r"|(为什么|为啥)?(打)?游戏(很|好|会|有点)?(卡|掉帧|不顺|lag)(怎么办)?|(怎么|怎样)让游戏(更)?(顺|流畅|快))$")
    BOOST_STATUS = re.compile(r"^游戏优化(状态|开着吗|有开吗|在做什么|做了什么)?$")
    BOOST_SWITCH = re.compile(r"^(?P<act>打开|开启|开|关掉|关闭|关|停止)(自动)?游戏优化$")

    GAME_YES = re.compile(r"^(把)?(?P<w>[^?？吗呢什么哪]{0,20}?)(记成|当成|当作|算是?|是)(一个|个)?游戏$")
    GAME_NO = re.compile(r"^(?P<w>[^?？吗呢什么哪]{0,20}?)(不是|不算)(一个|个)?游戏$")
    GAME_RENAME = re.compile(r"^(把)?(?P<a>[^?？吗]{1,20}?)(叫做|改名叫|改名为|改叫|改名成)(?P<b>[^?？吗]{1,20})$")
    GAME_LIST = re.compile(r"^(你)?(认识|学过|记得|记住了)(哪些|什么|几个)游戏(了)?$")

    def _learn_games(self, sentence: str) -> str | None:
        known = self.known_games
        if known is None:
            return None
        if self.GAME_LIST.match(sentence):
            return known.describe()
        try:
            found = self.GAME_NO.match(sentence)
            if found:
                return known.mark_not_game(found.group("w"))
            found = self.GAME_YES.match(sentence)
            if found:
                return known.mark_game(found.group("w"))
            found = self.GAME_RENAME.match(sentence)
            if found and known._pick(found.group("a")) in known.games:  # 只认学过的游戏(“把我叫做老板”不归这里)
                return known.rename(found.group("a"), found.group("b"))
        except ValueError as e:
            return str(e)
        return None

    TEMP_RE = re.compile(r"^(电脑|主机|CPU|cpu|处理器)(现在)?(的)?(温度|多少度|几度|热不热|会不会太热|有没有过热)(多少|怎么样|高不高)?$"
                         r"|^(现在)?(电脑|主机)?(温度|几度)(怎么样|多少|高不高)$")

    REPORT_RE = re.compile(r"^(游戏报告|上一局报告|刚才那局电脑(怎么样|有没有问题)|(刚才)?打游戏(的)?时候电脑(怎么样|有没有问题)"
                           r"|你(刚才)?(打游戏时)?(用了|占了)多少(资源|CPU|cpu|内存))$")

    WISHLIST_RE = re.compile(r"^(steam|Steam|STEAM)?(的)?(愿望单|愿望清单)(里)?(有什么|有没有|有哪些|哪些)?(游戏)?(在)?(打折|特价|降价)(吗|了吗|了没)?$"
                             r"|^(steam|Steam|STEAM)(上)?(有什么|有哪些|哪些)?(游戏)?(在)?(打折|特价)(吗)?$|^(看看|查查)?(我的)?(steam|Steam)?愿望单$")

    SELFCHECK_RE = re.compile(r"^(做一?次|做个|来个|帮我)?(自我检查|自检|体检一下你自己|检查一下你自己|你有没有(坏掉|出问题)|你哪里坏了)(一下)?$")

    DUCK_TEST_RE = re.compile(r"^(测试|试试|试一下|测一下)(一下)?(压低|降低|调低)(电脑)?(声音|音量)$")

    DUCK_LOG_RE = re.compile(r"^(按住说话|压低声音)(的)?(诊断|记录|日志)$")

    def _games(self, sentence: str) -> str | None:
        if self.party is not None:
            import friday_partymode

            if friday_partymode.START_RE.match(sentence):
                return self.party[0]()
            if friday_partymode.STOP_RE.match(sentence):
                return self.party[1]()
        if self.DUCK_LOG_RE.match(sentence):
            ducker = getattr(self, "ducker", None)
            if ducker is None:
                return "按住说话没有开着(或这台电脑不是 Windows)。"
            if not ducker.log:
                return ("这次打开星期五以后,按住说话还没有叫过“压低声音”。如果你刚按过 F9 而且听到“嘀”一声,"
                        "把这句截图给我。")
            return "按住说话最近几次:\n" + "\n".join(ducker.log)
        if os.name == "nt" and self.DUCK_TEST_RE.match(sentence):
            import friday_audio

            return friday_audio.test_duck()
        if self.selfcheck is not None and self.SELFCHECK_RE.match(sentence):
            import friday_selfcheck

            return friday_selfcheck.SelfCheck.describe(self.selfcheck.run())
        learned = self._learn_games(sentence)
        if learned:
            return learned
        if self.dota_advice is not None:
            try:
                advice = self.dota_advice.answer(sentence)
            except ValueError as e:
                return str(e)
            if advice:
                return advice
        if self.temps is not None and self.TEMP_RE.match(sentence):
            return self.temps.describe()
        if self.gamereport is not None and self.REPORT_RE.match(sentence):
            return self.gamereport.describe()
        if self.wishlist is not None and self.WISHLIST_RE.match(sentence):
            try:
                return self.wishlist.describe()
            except ValueError as e:
                return str(e)
        if self.gameboost is not None:
            if self.GAME_CHECK.match(sentence):
                import friday_gameboost

                return friday_gameboost.checkup()
            if self.BOOST_STATUS.match(sentence):
                return self.gameboost.describe()
            found = self.BOOST_SWITCH.match(sentence)
            if found:
                on = found.group("act") in ("打开", "开启", "开")
                os.environ["FRIDAY_GAME_BOOST"] = "on" if on else "off"
                if self.env_file:
                    from pathlib import Path

                    from friday_discord import save_env_value

                    save_env_value(Path(self.env_file), "FRIDAY_GAME_BOOST", "on" if on else "off")
                if not on:
                    self.gameboost.stop()
                return ("好,以后开游戏时自动优化:游戏优先、后台程序让路、电源换高性能,打完改回来。" if on
                        else "好,游戏优化关掉了(改过的都已经改回原样)。")
        if self.dota is not None and getattr(self.toolbox, "steam", None) is not None:
            if self.DOTA_LAST.match(sentence):
                return self.dota.last_match()
            if self.DOTA_RECENT.match(sentence):
                return self.dota.summary()
            if self.DOTA_HEROES.match(sentence):
                return self.dota.best_heroes()
            if self.DOTA_RANK.match(sentence):
                return self.dota.profile()
        if self.apex is not None:
            if self.APEX_MAP.match(sentence):
                return self.apex.maps()
            if self.APEX_RANK.match(sentence):
                return self.apex.player()
        return None

    # ---------- 连接的应用 ----------
    NEWS_RE = re.compile(r"^(今天|最新|最近|现在)?(有)?(什么|啥|哪些)?(?P<topic>马来西亚|大马|本地|国内|国际|世界|国外|科技|财经|经济|股市|体育|娱乐)?"
                         r"(的)?(新闻|头条|时事)(吗|呢)?$")
    NEWS_ABOUT = re.compile(r"^(关于|有关)(?P<q>.{1,15}?)的新闻$|^(?P<q2>.{1,15}?)(有什么|的)?最新新闻$")
    STEAM_RECENT = re.compile(r"^(我)?(最近|这两周|最近两周)(在)?(steam|Steam|STEAM)?(上)?(玩了|玩)(什么|哪些)(游戏)?$"
                              r"|^(steam|Steam)(最近)?(玩了什么|记录|游戏时间)$")
    STEAM_FRIENDS = re.compile(r"^(steam|Steam)?(上)?(有)?(哪些|哪个|谁)?(好友|朋友)(在线|在玩什么|在玩)(吗)?$|^(谁|哪些朋友)在(线|玩游戏)$")
    STEAM_TOP = re.compile(r"^(我)?(玩得?最久|玩最多)的(游戏|是什么游戏)$|^(steam|Steam)(游戏)?(统计|总时间)$")
    GAME_TIME = re.compile(r"^(我)?(?P<g>[A-Za-z0-9一-鿿 ]{2,24}?)(一共|总共|总的)?(玩了|打了)(多久|多少小时|多少时间|几个小时|几小时)$")
    GPU_RE = re.compile(r"^(显卡|GPU|gpu|N卡|n卡|英伟达|NVIDIA|nvidia)(的)?(温度|状态|怎么样|多少度|使用率|情况|热不热)(多少)?$")
    REPLAY_RE = re.compile(r"^(把)?(刚才|刚刚)(那段|那个|那波|的)?(存|保存|录)(下来|起来)$|^(存|保存|录)(一下)?(精彩片段|回放|高光|刚才那段)$"
                           r"|^(精彩片段|即时回放|即时重放)$")
    RECORD_RE = re.compile(r"^(开始|停止|结束)(游戏)?(录像|录屏|录制)$")
    GAME_SHOT_RE = re.compile(r"^(游戏截图|N卡截图|n卡截图|NVIDIA截图|nvidia截图)$")
    WECHAT_RE = re.compile(r"^(打开|开|切到|切换到|去|回到)(微信|wechat|WeChat|weixin)$")
    BACKUP_RE = re.compile(r"^(马上|立刻|现在)?(备份|备份一下)(文件夹|重要文件夹|我的文件)$|^(文件夹)?备份(状态|情况|好了没)$|^(马上|立刻|现在)备份$")
    SPOTIFY_NOW = re.compile(r"^(现在|这首)?(放的是|在放|播放的是|是)?(什么歌|哪首歌|谁的歌)$|^这首歌叫什么$")
    SPOTIFY_PLAY = re.compile(r"^(播放|放|来一首|放一首|我想听|想听|帮我放|放首)(一下)?(?P<q>.{1,30})$")
    NOT_SONGS = ("音乐", "歌", "一下", "大", "小", "假", "心", "学", "弃", "松", "屁", "下", "进", "到", "着", "开", "在", "过")

    ORB_ON = re.compile(r"^(显示|打开|开|叫出|拿出)(桌面)?(的)?(小光球|悬浮球|小球|光球)$")
    ORB_OFF = re.compile(r"^(收起|关掉|关闭|隐藏|藏起|拿掉)(桌面)?(的)?(小光球|悬浮球|小球|光球)$")

    def _apps(self, sentence: str) -> str | None:
        if self.floatorb is not None:
            if self.ORB_ON.match(sentence):
                return self.floatorb(True)
            if self.ORB_OFF.match(sentence):
                return self.floatorb(False)
        if self.news is not None:
            found = self.NEWS_RE.match(sentence)
            if found:
                import friday_news

                topic = friday_news.topic_of(found.group("topic") or "")
                return self.news.topic(topic) if topic else self.news.today()
            found = self.NEWS_ABOUT.match(sentence)
            if found:
                return self.news.about(found.group("q") or found.group("q2"))
        if self.steam is not None:
            if self.STEAM_RECENT.match(sentence):
                return self.steam.recent()
            if self.STEAM_FRIENDS.match(sentence):
                return self.steam.friends()
            if self.STEAM_TOP.match(sentence):
                return self.steam.top()
            found = self.GAME_TIME.match(sentence)
            if found and found.group("g").strip() not in ("今天", "昨天", "这周", "本周", "游戏", "电脑", "这个星期"):
                return self.steam.game_time(found.group("g"))
        if self.nvidia:
            import friday_nvidia

            if self.GPU_RE.match(sentence):
                return friday_nvidia.describe(friday_nvidia.status())
            if self.REPLAY_RE.match(sentence):
                return friday_nvidia.hotkey("replay")
            if self.RECORD_RE.match(sentence):
                return friday_nvidia.hotkey("record")
            if self.GAME_SHOT_RE.match(sentence):
                return friday_nvidia.hotkey("screenshot")
        if self.WECHAT_RE.match(sentence):
            return self._wechat(sentence.startswith(("切", "回")))
        if self.folder_backup is not None and self.BACKUP_RE.match(sentence):
            backup, run_now = self.folder_backup
            if "状态" in sentence or "情况" in sentence or "好了没" in sentence:
                return backup.status()
            return run_now()
        if self.spotify is not None and self.spotify.connected:
            if self.SPOTIFY_NOW.match(sentence):
                return self.spotify.now_playing()
            found = self.SPOTIFY_PLAY.match(sentence)
            if found and found.group("q") not in self.NOT_SONGS and not found.group("q").startswith(("音乐", "一首歌")):
                return self.spotify.play(found.group("q"))
        return None

    def _wechat(self, switch_first: bool) -> str | None:
        computer = getattr(self.toolbox, "computer", None)
        if computer is None or not getattr(computer, "available", False):
            return None
        for name in ("微信", "WeChat", "Weixin"):
            try:
                result = computer.do("focus_window", name)
            except Exception:
                result = ""
            if result and result.startswith("已切到"):
                return "已切到微信。"
        for name in ("微信", "WeChat", "Weixin"):
            try:
                result = computer.do("open_app", name)
            except Exception:
                continue
            if result.startswith("已打开"):
                return "已打开微信。"
        return None  # 没找到:交给 Claude(它会再想办法)

    # ---------- 剪贴板历史 ----------
    CLIP_LIST = re.compile(r"^(看看?|打开)?(我的)?(剪贴板|剪切板)(历史|记录)?$|^(我)?(最近|刚才|之前)?(复制|拷贝)(过|了)?(什么|哪些)(东西)?$"
                           r"|^最近复制的(东西)?$")
    CLIP_COPY = re.compile(r"^(复制|拿回|贴回|放回|还原|再复制)(剪贴板(历史)?(里|的)?)?第(?P<n>\d{1,2}|[一二三四五六七八九十]{1,3})(个|段|条)$")
    CLIP_FIND = re.compile(r"^(在)?(剪贴板|剪切板)(历史)?(里|里面)?(找|搜)(一下)?(?P<q>.{1,20})$")
    CLIP_KIND = re.compile(r"^(把)?(刚才|刚刚|之前|上次|最近)?(复制|拷贝)的?(那个|那条|那段)?(?P<k>网址|链接|网页|link|电话|号码|电话号码|邮箱|email|电邮)(拿回来|放回去)?$", re.I)
    CLIP_CLEAR = re.compile(r"^(清空|清除|删掉|删除)(剪贴板|剪切板)(历史|记录)?$")

    def _clipboard(self, sentence: str) -> str | None:
        clips = self.clips
        if clips is None:
            return None
        if self.CLIP_CLEAR.match(sentence):
            return clips.clear()
        if self.CLIP_LIST.match(sentence):
            return clips.describe()
        found = self.CLIP_COPY.match(sentence)
        if found:
            import friday_desktop
            import friday_focus

            n = found.group("n")
            return clips.copy_back(int(n) if n.isdigit() else friday_focus.cn_number(n) or 0, friday_desktop.write_clipboard)
        found = self.CLIP_FIND.match(sentence)
        if found:
            return clips.describe(clips.find(found.group("q")))
        found = self.CLIP_KIND.match(sentence)
        if found:
            import friday_desktop

            kind = {"网址": "url", "链接": "url", "网页": "url", "link": "url", "邮箱": "email", "email": "email",
                    "电邮": "email"}.get(found.group("k").lower(), "number")
            hits = clips.find(kind=kind)
            if not hits:
                return "剪贴板历史里没有这样的。"
            return clips.copy_back(hits[0][0], friday_desktop.write_clipboard)
        return None

    # ---------- 习惯 ----------
    HABIT_REPORT = re.compile(r"^(?P<when>今天|这周|本周|这个星期)?(的)?(习惯|打卡)(情况|统计|记录|怎么样|完成了吗|完成得怎么样)?$")
    HABIT_UNDO = re.compile(r"^(撤销|取消)(刚才|上一次|上次)?(的)?(?P<n>[一-鿿]{1,6}?)打卡$")

    def _habits(self, sentence: str) -> str | None:
        habits = self.habits
        if habits is None or not habits.items:
            return None
        found = self.HABIT_REPORT.match(sentence)
        if found:
            week = (found.group("when") or "") in ("这周", "本周", "这个星期")
            return habits.report("week" if week else "today")
        found = self.HABIT_UNDO.match(sentence)
        if found:
            return habits.undo(found.group("n"))
        hit = habits.match_sentence(sentence)
        if hit:
            habit, count = hit
            return habits.check_in(habit["name"], count)
        return None

    # ---------- 网络 ----------
    NET_SPEED = re.compile(r"^(测|测试|测量|看看|查|查查)(一下|一测)?(网速|网络速度|网络|速度|延迟|ping)$"
                           r"|^(网速|网络|延迟|ping)(怎么样|多少|快不快|高不高|好不好)$", re.I)
    NET_OUTAGE = re.compile(r"^(?P<when>今天|这周|本周|最近)?(断了?几次网|网络断了几次|断网(记录|几次|了几次)|有没有断网|网断过吗|断过网吗)$")

    def _network(self, sentence: str) -> str | None:
        if self.speed is not None and self.NET_SPEED.match(sentence):
            return self.speed()
        found = self.NET_OUTAGE.match(sentence)
        if found and self.netwatch is not None:
            week = (found.group("when") or "") in ("这周", "本周", "最近")
            return self.netwatch.report("week" if week else "today")
        return None

    # ---------- 汇率 ----------
    def _money(self, sentence: str) -> str | None:
        if self.fx is None or len(sentence) > 30:
            return None
        import friday_fx

        rates, alerts = self.fx
        if re.fullmatch(r"(我的)?汇率提醒(有哪些|列表)?", sentence):
            return alerts.describe()
        found = re.fullmatch(r"(取消|删掉|删除|不要)(?P<c>.{1,6}?)(的)?汇率提醒", sentence)
        if found:
            codes = friday_fx.mentions(found.group("c"))
            return alerts.remove(codes[0]) if codes else None
        alert = friday_fx.parse_alert(sentence)
        if alert:
            base, target, quote, direction = alert
            return alerts.add(base, target, quote, direction)
        convert = friday_fx.parse_convert(sentence)
        if convert:
            return rates.convert(*convert)
        if "汇率" in sentence and not re.search(r"\d", sentence):
            codes = [c for c in friday_fx.mentions(sentence) if c != "MYR"]
            if len(codes) == 1:
                return rates.describe_rate(codes[0])
        return None

    # ---------- 专注 ----------
    def _focus_start(self, found):
        if self.focus is None:
            return None
        minutes = None
        if found.group("m"):
            import friday_focus

            minutes = friday_focus.cn_number(found.group("m") + (found.group("u") or "分钟"))
            if minutes is None:
                return None
        try:
            return self.focus.start(minutes)
        except ValueError as e:
            return str(e)

    def _focus_stop(self, found):
        return self.focus.stop() if self.focus is not None else None

    def _focus_status(self, found):
        if self.focus is None or not (self.focus.active() or self.focus.break_until):
            return None
        return self.focus.status()

    def _focus_report(self, found):
        if self.focus is None:
            return None
        week = (found.group("when") or "") in ("这周", "本周", "这个星期")
        return self.focus.report("week" if week else "today")

    # ---------- 简报 ----------
    def _brief(self, found):
        return self.brief() if self.brief is not None else None

    # ---------- 找文件 ----------
    def _find_file(self, found):
        if getattr(self.toolbox, "file_roots", None) is None:
            return None
        text, error = self.toolbox._find_files({"action": "find", "words": [found.group("q")]}, lambda s: None)
        return None if error else text

    def _open_found(self, found):
        if not getattr(self.toolbox, "found_files", None):
            return None
        import friday_focus

        n = found.group("n")
        number = int(n) if n.isdigit() else friday_focus.cn_number(n)
        text, error = self.toolbox._find_files({"action": "open", "id": number}, lambda s: None)
        return text

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
