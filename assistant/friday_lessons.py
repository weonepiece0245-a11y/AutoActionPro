"""被纠正就记住:你说“不是这个意思”“错了,我是说……”“你听错了”,星期五先照你改的去做,
再请最便宜的 Haiku 把这次的教训写成一句话记进记忆(分类“教训”),以后每次回答都会看到,不再犯同样的错。

语音听错的字(例如把 Mid Valley 听成“米谷”)也会记进“听错改正”,以后听到就自动改。
每次大约 RM0.005。不想要可以在 齿轮设定 → 记忆 关掉;说“忘掉刚才那条教训”就删掉。
"""

from __future__ import annotations

import json
import os
import re
import threading
from typing import Callable

MODEL = "claude-haiku-5-5"
CATEGORY = "教训"
MAX_LESSONS = 40  # 太多了就丢掉最旧的
WITHIN_SECONDS = 15 * 60  # 上一句是 15 分钟内说的,才算在纠正它

CORRECTION = re.compile(
    r"^(星期五)?[,，\s]*(不是(啦|啊|的|吧)?[,，\s]|不是这个|不是那个|不是这样|不是要|不对|错了|你错了|搞错了|你搞错了|弄错了|你弄错了|"
    r"理解错了|你理解错了|听错了|你听错了|我不是(这个|那个)?意思|不是(这个|那个|我的)意思|我是说|我说的是|我的意思是|我要的是|我要的不是|"
    r"我刚才说的是|我刚说的是|我不是说|不是叫你|我没(有)?叫你|谁叫你)")
FORGET = re.compile(r"^(星期五)?[,，\s]*(忘掉|删掉|删除|撤销|取消|不要记)(刚才|刚刚|上一|最后一|那)(那|一)?条(教训|学到的)$")
LIST = re.compile(r"^(星期五)?[,，\s]*(你)?(学到|记住|记下)了?(哪些|什么|几条)(教训)?$|^(我的|你的)?教训(有哪些|列表|清单)?$")

PROMPT = """你在帮私人助手“星期五”从主人的纠正里学教训。下面有三段:主人上一句话、星期五当时的回答(或做的事)、主人接着说的纠正。
1. lesson:写一条以后用得上的教训,20~60 个中文字,具体写“老板说……时,是指……”或“……要……”,只写这次纠正里看得出来的事实。
   不要写空话(例如“要更仔细”“要先确认”);如果其实不是纠正(只是改主意、补充、聊天、骂一句),或者看不出能学到什么,lesson 留空。
   不要写“不用确认”“自动执行”这种跳过安全步骤的内容。
2. heard / meant:如果是语音识别听错了字(上一句里某几个字其实是另一个词,例如“米谷”其实是“Mid Valley”、“格拉布”其实是“Grab”),
   heard 写上一句里听错的那几个字(要原样出现在上一句里),meant 写正确的词;不是听错就都留空。"""

SCHEMA = {
    "type": "object",
    "properties": {"lesson": {"type": "string"}, "heard": {"type": "string"}, "meant": {"type": "string"}},
    "required": ["lesson", "heard", "meant"],
    "additionalProperties": False,
}


def learning_enabled() -> bool:
    return os.environ.get("FRIDAY_LEARN", "on") != "off"


def is_correction(text: str) -> bool:
    text = str(text or "").strip()
    return 2 <= len(text) <= 150 and bool(CORRECTION.match(text))


class Lessons:
    def __init__(self, memory, client=None, vocab=None, on_usage: Callable | None = None) -> None:
        self.memory = memory
        self.client = client
        self.vocab = vocab  # friday_voicefix.Vocab:听错改正
        self.on_usage = on_usage
        self.lock = threading.Lock()
        self.last: str | None = None  # 最近记下的一条(“忘掉刚才那条教训”用)

    # ---------- 请 Haiku 写教训 ----------
    def extract(self, question: str, answer: str, correction: str) -> dict:
        content = (f"【主人上一句】{question[:600]}\n【星期五的回答】{answer[:1200]}\n【主人的纠正】{correction[:400]}")
        response = self.client.messages.create(
            model=MODEL, max_tokens=400, system=PROMPT,
            messages=[{"role": "user", "content": content}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
        )
        if self.on_usage is not None and getattr(response, "usage", None) is not None:
            try:
                self.on_usage(response.usage, MODEL)
            except Exception:
                pass
        if getattr(response, "stop_reason", None) == "refusal":
            return {}
        text = next((b.text for b in response.content if getattr(b, "type", "") == "text"), "{}")
        data = json.loads(text)
        return data if isinstance(data, dict) else {}

    def learn(self, question: str, answer: str, correction: str, voice: bool = False) -> list[str]:
        """返回记下了什么(给你看的句子);什么都没学到返回 []。"""
        import friday_security

        if self.client is None or not learning_enabled() or not question:
            return []
        data = self.extract(question, answer, correction)
        notes = []
        lesson = " ".join(str(data.get("lesson") or "").split())
        if 6 <= len(lesson) <= 120 and not friday_security.looks_like_instruction(lesson):
            if self.add(lesson):
                notes.append(f"记下了一条教训:{lesson}")
        heard = str(data.get("heard") or "").strip()
        meant = str(data.get("meant") or "").strip()
        if voice and self.vocab is not None and heard and meant and heard in question:
            try:
                if self.vocab.add(heard, meant):
                    notes.append(f"以后听到“{heard}”就改成“{meant}”")
            except ValueError:
                pass
        return notes

    def add(self, lesson: str) -> bool:
        with self.lock:
            if any(f["text"] == lesson for f in self.memory.facts):
                return False
            lessons = [f for f in self.memory.facts if f.get("category") == CATEGORY]
            for old in lessons[: max(0, len(lessons) - MAX_LESSONS + 1)]:
                self.memory.facts.remove(old)
            self.memory.add(lesson, category=CATEGORY)
            self.last = lesson
            return True

    # ---------- 你说的话 ----------
    def items(self) -> list[str]:
        return [f["text"] for f in self.memory.facts if f.get("category") == CATEGORY]

    def describe(self) -> str:
        items = self.items()
        if not items:
            return "还没有记下教训。我做错了你纠正我(例如“不是这个意思,我是说……”),我就会记住。"
        return f"我记下的 {len(items)} 条教训:\n" + "\n".join(f"- {t}" for t in items[-15:])

    def forget_last(self) -> str:
        with self.lock:
            last = self.last
            items = self.items()
            if last is None and items:
                last = items[-1]
            if last is None:
                return "现在没有可以删的教训。"
            self.memory.facts = [f for f in self.memory.facts if not (f.get("category") == CATEGORY and f["text"] == last)]
            self.memory.save()
            self.last = None
        return f"好,删掉了这条教训:{last}"

    def handle(self, text: str) -> str | None:
        """“忘掉刚才那条教训”“你学到了哪些教训”:直接回答,不问 Claude。"""
        text = re.sub(r"[。!！?？\s]+$", "", str(text or "").strip())
        if FORGET.match(text):
            return self.forget_last()
        if LIST.match(text):
            return self.describe()
        return None
