"""自动选模型:日常的事交给 Sonnet 5.5(便宜一半),难的事交给 Opus 5.5。

怎么尽量不选错(宁可多花一点,也不让难题被简单处理):
1. 规则先判断:明显难的(分析、规划、写长文、钱/健康/法律……)直接用 Opus;
   明显简单的(设提醒、几点、天气、开程序……)直接用 Sonnet,不用问 Haiku。
2. 规则判断不了的才问 Haiku 5.5;Haiku 要给出把握程度,把握不到 80% 一律用 Opus;Haiku 出错也用 Opus。
3. 同一个话题里只会升级、不会降级(换模型会让缓存失效,也带不走思考记录)。
4. Sonnet 自己发现问题比想象的难,可以在回答前把问题转给 Opus。
5. 你说“认真想想”“用最强的”,马上换 Opus,并且把刚才那个问题记下来当例子,下次 Haiku 会参考。
6. 窗口里可以切换:自动 / 只用 Opus / 只用 Sonnet。
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Callable

OPUS = "claude-opus-5-5"
SONNET = "claude-sonnet-5-5"
HAIKU = "claude-haiku-5-5"
LABELS = {OPUS: "Opus 5.5", SONNET: "Sonnet 5.5"}
MODES = ("auto", "opus", "sonnet")
NEW_TOPIC_IDLE = 30 * 60  # 隔了这么久再问,就当新话题(这时缓存也早过期了)
SONNET_MIN_CONFIDENCE = 80
MAX_EXAMPLES = 20

UPGRADE_WORDS = ("认真想", "仔细想", "好好想", "深入想", "想清楚", "认真点", "仔细点", "用最强", "最强的模型",
                 "用opus", "换opus", "opus回答", "你再想想", "再想一下", "想深一点")
HARD_WORDS = (
    "分析", "规划", "计划", "策略", "方案", "比较", "对比", "评估", "利弊", "优缺点", "权衡", "建议",
    "怎么办", "该不该", "值不值", "决定", "选择哪", "哪个好", "写一篇", "写一份", "文章", "报告", "论文",
    "总结", "整理成", "代码", "程序", "编程", "bug", "脚本", "公式", "计算", "算一下", "合同", "投资", "理财",
    "股票", "贷款", "保险", "税", "医生", "病", "药", "症状", "法律", "律师", "翻译", "为什么", "原理",
    "解释一下", "教我", "学习", "简历", "面试", "邮件回复", "回复这封",
)
SIMPLE_WORDS = (
    "提醒我", "提醒一下", "闹钟", "几点", "现在时间", "天气", "下雨", "气温", "打开", "关掉", "关闭", "音量",
    "大声", "小声", "播放", "暂停", "下一首", "截图", "截个图", "待办", "日程", "有什么安排", "记住",
    "你好", "早安", "午安", "晚安", "谢谢", "在吗", "哈哈", "好的", "收到", "没事了", "今天几号", "星期几",
)
SIMPLE_MAX_CHARS = 30
HARD_MIN_CHARS = 150  # 一大段话通常是复杂的事

CLASSIFY_PROMPT = """你负责给私人助手“星期五”分配模型。用户的一个新话题,要决定交给哪个模型回答:
- sonnet:便宜一半,适合日常的事:闲聊、打招呼、查天气/时间/新闻、简单的事实问题、设提醒和待办、
  打开或关闭程序、播放音乐、查日程、发一封内容简单的消息、简单搜索。
- opus:更强更贵,适合需要动脑的事:分析、比较、规划、建议、做决定、写文章/报告/长邮件、
  编程和计算、学习和解释原理、多步骤的复杂任务,以及任何关于钱、健康、法律、工作的重要事情。

规则:
1. 宁可错用 opus,也不要把需要动脑的事交给 sonnet。拿不准就选 opus。
2. confidence 是你对“这个选择是对的”有多大把握(0~100)。只有非常确定是日常小事,sonnet 才给 80 以上。
3. 只看任务本身难不难,不要被语气客气或者问题很短误导(“帮我想想要不要换工作”很短,但要用 opus)。

{examples}只输出 JSON。"""

DEFAULT_EXAMPLES = [
    ("明天会下雨吗", "sonnet"), ("提醒我八点打电话给妈妈", "sonnet"), ("帮我打开 Discord", "sonnet"),
    ("今天有什么新闻", "sonnet"), ("Apex 新赛季什么时候开始", "sonnet"), ("帮我把明天的会议加进日历", "sonnet"),
    ("帮我想想要不要换工作", "opus"), ("这个月花太多了,帮我看看怎么省钱", "opus"),
    ("帮我写一封请假邮件给老板,理由是家里有事", "opus"), ("Dota 2 这个英雄怎么出装比较好,分析一下", "opus"),
    ("我最近一直头痛,会不会有问题", "opus"), ("帮我整理一下这周要做的事,排个优先顺序", "opus"),
]

SCHEMA = {
    "type": "object",
    "properties": {
        "model": {"type": "string", "enum": ["sonnet", "opus"]},
        "confidence": {"type": "integer"},
        "reason": {"type": "string"},
    },
    "required": ["model", "confidence", "reason"],
    "additionalProperties": False,
}

# 给 Sonnet 用的工具:发现问题比想象的难,就在回答前转给 Opus
UPGRADE_TOOL = {
    "name": "switch_to_opus",
    "description": (
        "把这个问题转给更强的 Opus 模型来回答。如果问题需要深入分析、多步骤规划、写长文、编程计算、"
        "复杂判断,或者涉及钱、健康、法律、工作上的重要决定,就在写任何回答之前调用这个工具,"
        "不要自己先回答。日常聊天、查资料、设提醒、操作电脑这些不需要调用。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {"reason": {"type": "string", "description": "一句话说明为什么需要更强的模型"}},
        "required": ["reason"],
        "additionalProperties": False,
    },
}


def _norm(text: str) -> str:
    return text.lower().replace(" ", "")


def rule(text: str) -> str | None:
    """规则判断:明显难 -> OPUS,明显简单 -> SONNET,判断不了 -> None。"""
    plain = _norm(text)
    if any(w in plain for w in UPGRADE_WORDS) or any(w in plain for w in HARD_WORDS) or len(text) >= HARD_MIN_CHARS:
        return OPUS
    if len(text) <= SIMPLE_MAX_CHARS and any(w in plain for w in SIMPLE_WORDS):
        return SONNET
    return None


def wants_upgrade(text: str) -> bool:
    plain = _norm(text)
    return any(w in plain for w in UPGRADE_WORDS)


class Router:
    def __init__(self, client, path: str | Path | None = None, clock: Callable[[], float] = time.monotonic,
                 on_change: Callable[[str, str], None] | None = None,
                 on_usage: Callable | None = None) -> None:
        self.client = client
        self.path = Path(path) if path else None
        self.clock = clock
        self.on_change = on_change  # (模型标签, 模式) 变了就通知窗口
        self.on_usage = on_usage  # Haiku 的用量也算进花费
        self.lock = threading.Lock()
        self.mode = "auto"
        self.examples: list[dict] = []
        self.current = OPUS
        self.last_ask: float | None = None
        self.last_question = ""
        self.last_reason = ""
        self._load()

    # ---------- 设置 ----------
    def _load(self) -> None:
        if self.path is None:
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if data.get("mode") in MODES:
            self.mode = data["mode"]
        self.examples = [e for e in data.get("examples", []) if isinstance(e, dict) and e.get("text")][-MAX_EXAMPLES:]

    def _save(self) -> None:
        if self.path is None:
            return
        temp = self.path.with_name(self.path.name + ".tmp")
        try:
            temp.write_text(json.dumps({"mode": self.mode, "examples": self.examples}, ensure_ascii=False),
                            encoding="utf-8")
            os.replace(temp, self.path)
        except OSError:
            pass

    def set_mode(self, mode: str) -> None:
        if mode not in MODES:
            raise ValueError(mode)
        self.mode = mode
        self._save()
        self._notify()

    def label(self) -> str:
        return LABELS.get(self.current, self.current)

    def _notify(self) -> None:
        if self.on_change is not None:
            try:
                self.on_change(self.label(), self.mode)
            except Exception:
                pass

    # ---------- 学习 ----------
    def learn(self, text: str, model: str = "opus") -> None:
        """记下一个选错的例子(Sonnet 回答了,但其实需要 Opus)。"""
        text = text.strip()[:200]
        if not text:
            return
        self.examples = [e for e in self.examples if e["text"] != text] + [{"text": text, "model": model}]
        self.examples = self.examples[-MAX_EXAMPLES:]
        self._save()

    # ---------- 选择 ----------
    def choose(self, question: str, history_empty: bool) -> str:
        """每个问题之前调用,返回这次用的模型。"""
        now = self.clock()
        new_topic = history_empty or self.last_ask is None or now - self.last_ask > NEW_TOPIC_IDLE
        previous = self.current
        if self.mode == "opus":
            model = OPUS
        elif self.mode == "sonnet":
            model = SONNET
        elif wants_upgrade(question):
            model = OPUS
            if previous == SONNET and not new_topic and self.last_question:
                self.learn(self.last_question)  # 用户嫌刚才那个回答不够好
        elif not new_topic:
            # 同一个话题:只升级不降级
            model = OPUS if previous == OPUS or rule(question) == OPUS else SONNET
        else:
            model = rule(question) or self._ask_haiku(question)
        self.current = model
        self.last_ask = now
        self.last_question = question
        if model != previous:
            self._notify()
        return model

    def upgrade(self, question: str) -> None:
        """Sonnet 自己要求转给 Opus。"""
        self.learn(question)
        self.current = OPUS
        self._notify()

    def _examples_text(self) -> str:
        examples = [(t, m) for t, m in DEFAULT_EXAMPLES] + [(e["text"], e["model"]) for e in self.examples]
        lines = "\n".join(f"- “{t}” -> {m}" for t, m in examples)
        return f"例子(后面的是这位用户自己的,更要参考):\n{lines}\n\n"

    def _ask_haiku(self, question: str) -> str:
        try:
            response = self.client.messages.create(
                model=HAIKU,
                max_tokens=1024,
                system=CLASSIFY_PROMPT.format(examples=self._examples_text()),
                messages=[{"role": "user", "content": f"用户的新话题:\n{question[:2000]}"}],
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
            )
            if self.on_usage is not None and getattr(response, "usage", None) is not None:
                try:
                    self.on_usage(response.usage, HAIKU)
                except Exception:
                    pass
            if getattr(response, "stop_reason", None) == "refusal":
                return OPUS
            text = next(b.text for b in response.content if getattr(b, "type", "") == "text")
            data = json.loads(text)
            self.last_reason = str(data.get("reason", ""))
            if data.get("model") == "sonnet" and int(data.get("confidence", 0)) >= SONNET_MIN_CONFIDENCE:
                return SONNET
            return OPUS
        except Exception:
            return OPUS  # 判断不了就用强的
