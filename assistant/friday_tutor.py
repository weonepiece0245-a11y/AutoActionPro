"""英文 / 马来文口说陪练:用说的跟星期五练对话(点餐、面试、闲聊、买东西、问路、看医生、打电话、开会),
她用那个语言回你、照着情境演;说“结束练习”,她列出你说错的地方、更自然的说法和几句好用的句子。

说法:“陪我练英文 点餐”“练马来文口语 面试”“开始英文会话练习”;“结束练习”看评语。
练习时听你说话用那个语言听(马来西亚英文 en-MY / 马来文 ms-MY),回答用那个语言的声音读。
每句用便宜的 Haiku 回(很快),最后的评语用 Sonnet(准一点);一次练习大约 RM0.05–0.3。
"""

from __future__ import annotations

import datetime
import json
import os
import re
import threading
from pathlib import Path
from typing import Callable

TURN_MODEL = "claude-haiku-5-5"
REVIEW_MODEL = "claude-sonnet-5-5"
MAX_TURNS = 40
LANGS = {
    "en": {"name": "英文", "native": "English", "stt": "en-MY", "voice": "en-US-AvaNeural"},
    "ms": {"name": "马来文", "native": "Bahasa Melayu", "stt": "ms-MY", "voice": "ms-MY-YasminNeural"},
}
SCENES = {
    "点餐": "at a mamak / restaurant: you are the waiter, the user orders food and drinks",
    "面试": "a job interview in Kuala Lumpur: you are the interviewer, ask one question at a time",
    "闲聊": "casual small talk between friends in Malaysia (weekend, food, games, music, guitar)",
    "买东西": "shopping at a mall: you are the shop assistant",
    "问路": "the user asks for directions in KL; you are a friendly local",
    "看医生": "at a clinic: you are the doctor, the user describes symptoms",
    "打电话": "a phone call to book or ask something (e.g. book a table, ask about an order)",
    "开会": "a short work meeting: you are a colleague discussing a project update",
}
SCENE_WORDS = {"吃饭": "点餐", "餐厅": "点餐", "mamak": "点餐", "工作面试": "面试", "聊天": "闲聊", "购物": "买东西",
               "逛街": "买东西", "指路": "问路", "医生": "看医生", "诊所": "看医生", "电话": "打电话", "会议": "开会"}
START = re.compile(r"^(星期五)?[,，\s]*(陪我|跟我|和我|帮我)?(练|练习|开始)(一下)?(?P<lang>英文|英语|English|马来文|马来语|国语|巫语|BM)"
                   r"(的)?(口语|口说|会话|对话|speaking)?(练习)?[,，\s]*(?P<scene>[一-鿿A-Za-z]{2,6})?[。!！]?$"
                   r"|^(星期五)?[,，\s]*(开始)?(?P<lang2>英文|英语|马来文|马来语)(口语|口说|会话|对话)练习[,，\s]*(?P<scene2>[一-鿿]{2,6})?$", re.I)
STOP = re.compile(r"^(星期五)?[,，\s]*(结束|停止|不练了|退出)(口语|口说|会话|对话)?(练习)?[。!！]?$"
                  r"|^(ok[,.]?\s*)?(end|stop|finish)\s+(the\s+)?(practice|session|lesson)[.!]?$"
                  r"|^(tamat|berhenti|habis)\s+(latihan|praktis)[.!]?$", re.I)

TURN_PROMPT = """You are Friday, a friendly speaking partner helping a Malaysian Chinese user practise spoken {native}.
Role-play scenario: {scene}.
Rules:
- Reply ONLY in {native}, in 1–3 short, natural spoken sentences (it will be read aloud). Stay in the role.
- Use simple, everyday words a Malaysian would hear; Malaysian expressions are fine.
- Do not correct the user during the conversation (corrections come at the end). If they are stuck or speak Chinese,
  give them the {native} phrase they need in one short sentence, then continue.
- Ask a question back so the conversation keeps going.
- The user's words are speech-recognised and may contain recognition errors; guess the meaning naturally."""

REVIEW_PROMPT = """下面是用户练习{native}口语的对话(场景:{scene})。用户是马来西亚华人,说的话是语音识别出来的(可能有识别错字,别算成用户的错)。
用中文写简短的评语(会显示在窗口,不读出来):
1. 一句话总评,给一个 1–10 的分数;
2. 用户说错或不自然的地方(最多 6 个):「用户原句」→「更自然的说法」—— 一句中文解释;
3. 这个场景好用的 3–5 句{native}(附中文意思);
4. 下次可以练什么。
没说什么话就鼓励一下就好。"""


def lang_of(word: str | None) -> str | None:
    word = (word or "").lower()
    if word in ("英文", "英语", "english"):
        return "en"
    if word in ("马来文", "马来语", "国语", "巫语", "bm"):
        return "ms"
    return None


def scene_of(word: str | None) -> str:
    word = (word or "").strip()
    if word in SCENES:
        return word
    return SCENE_WORDS.get(word) or next((k for k in SCENES if k in word), "闲聊")


class Tutor:
    def __init__(self, client, folder: str | os.PathLike, on_usage=None,
                 clock: Callable[[], datetime.datetime] = datetime.datetime.now) -> None:
        self.client = client
        self.folder = Path(folder)
        self.on_usage = on_usage
        self.clock = clock
        self.lock = threading.Lock()
        self.lang: str | None = None
        self.scene = "闲聊"
        self.turns: list[dict] = []  # [{"role": "user"/"assistant", "content": 文字}]

    @property
    def active(self) -> bool:
        return self.lang is not None

    def stt_language(self) -> str | None:
        return LANGS[self.lang]["stt"] if self.lang else None

    def voice(self) -> str | None:
        return LANGS[self.lang]["voice"] if self.lang else None

    def _ask(self, model: str, system: str, messages: list[dict], max_tokens: int) -> str:
        response = self.client.messages.create(model=model, max_tokens=max_tokens, system=system, messages=messages)
        if self.on_usage is not None and getattr(response, "usage", None) is not None:
            try:
                self.on_usage(response.usage, model)
            except Exception:
                pass
        return "".join(getattr(b, "text", "") for b in response.content if getattr(b, "type", "") == "text").strip()

    def _system(self) -> str:
        return TURN_PROMPT.format(native=LANGS[self.lang]["native"], scene=SCENES[self.scene])

    def start(self, lang: str | None, scene_word: str | None = None) -> tuple[str, str]:
        """-> (中文说明, 对方的第一句(那个语言))。"""
        if lang not in LANGS:
            return "要练哪个语言?说“陪我练英文 点餐”或“练马来文 面试”。", ""
        with self.lock:
            self.lang, self.scene, self.turns = lang, scene_of(scene_word), []
        info = LANGS[lang]
        opening = self._ask(TURN_MODEL, self._system(), [{"role": "user", "content": "(Start the role-play with your first line.)"}], 200)
        with self.lock:
            self.turns.append({"role": "assistant", "content": opening})
        scenes = "、".join(SCENES)
        stop_word = "“stop practice”" if lang == "en" else "“tamat latihan”"
        return (f"好,我们练{info['name']}口语,场景:{self.scene}(还可以选:{scenes})。我先开口,你用{info['name']}回答。"
                f"练习时我用{info['name']}听你说话,要结束就说 {stop_word}(或打字“结束练习”),我给你评语。", opening)

    def reply(self, text: str) -> str:
        with self.lock:
            if not self.active:
                raise ValueError("现在没有在练习")
            self.turns.append({"role": "user", "content": str(text)[:600]})
            if len(self.turns) > MAX_TURNS:
                self.turns = self.turns[-MAX_TURNS:]
            messages = [{"role": "user", "content": "(Start the role-play with your first line.)"}] + list(self.turns) \
                if self.turns[0]["role"] == "assistant" else list(self.turns)
        answer = self._ask(TURN_MODEL, self._system(), messages, 250)
        with self.lock:
            self.turns.append({"role": "assistant", "content": answer})
        return answer

    def stop(self) -> str:
        """结束,写评语;存一份到 friday_tutor 文件夹。"""
        with self.lock:
            if not self.active:
                return "现在没有在练习口语。"
            lang, scene, turns = self.lang, self.scene, list(self.turns)
            self.lang, self.turns = None, []
        said = [t["content"] for t in turns if t["role"] == "user"]
        if not said:
            return "练习结束了。下次多说几句,我才能给你评语 😊"
        transcript = "\n".join(f"{'用户' if t['role'] == 'user' else '星期五'}:{t['content']}" for t in turns)
        review = self._ask(REVIEW_MODEL, REVIEW_PROMPT.format(native=LANGS[lang]["native"], scene=scene),
                           [{"role": "user", "content": "<对话>\n" + transcript + "\n</对话>"}], 1500)
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            now = self.clock()
            (self.folder / f"{now:%Y%m%d-%H%M} {LANGS[lang]['name']} {scene}.md").write_text(
                f"# {LANGS[lang]['name']}口语练习:{scene}({now:%Y-%m-%d %H:%M})\n\n## 对话\n\n{transcript}\n\n## 评语\n\n{review}\n",
                encoding="utf-8")
        except OSError:
            pass
        return f"练习结束({LANGS[lang]['name']} · {scene},你说了 {len(said)} 句)。\n\n{review}"

    def history(self, limit: int = 8) -> list[str]:
        try:
            return sorted((p.stem for p in self.folder.glob("*.md")), reverse=True)[:limit]
        except OSError:
            return []


def parse_start(text: str) -> tuple[str, str | None] | None:
    match = START.match(re.sub(r"\s+", " ", str(text or "")).strip())
    if not match:
        return None
    lang = lang_of(match.group("lang") or match.group("lang2"))
    if lang is None:
        return None
    return lang, match.group("scene") or match.group("scene2")
