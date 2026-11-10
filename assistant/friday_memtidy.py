"""记忆自动整理:每周请最便宜的 Haiku 看一遍星期五记得的事,提出“哪些可以合并、哪些可能过时、哪些分类放错了”。

只是建议:你在齿轮设定 → 记忆整理 里勾选同意的,才会真的改。每次大约不到 RM0.05。
"""

from __future__ import annotations

import datetime
import json
import os
from pathlib import Path
from typing import Callable

MODEL = "claude-haiku-5-5"
EVERY_DAYS = 7
MIN_FACTS = 8
CATEGORIES = ["个人信息", "偏好", "习惯", "项目", "日程", "人际", "其他"]

PROMPT = """你在帮一位私人助手整理它对主人的长期记忆。今天是 {today}。
下面是编号的记忆(后面是记下的日期和分类)。请找出:
1. merge:说的是同一件事、或者重复的几条,合成一条(ids 列出要合并的编号,text 是合并后的一句话,保留所有有用的细节);
2. outdated:很可能已经过时的(例如日期已经过去的一次性安排“下周去日本”、明显被后来的记忆取代的),写清楚原因;
3. recategorize:分类明显放错的,给出正确分类。
拿不准的就不要提。没有要改的就返回空列表。不要编造记忆里没有的内容。"""

SCHEMA = {
    "type": "object",
    "properties": {
        "merge": {"type": "array", "items": {
            "type": "object",
            "properties": {"ids": {"type": "array", "items": {"type": "integer"}}, "text": {"type": "string"},
                           "category": {"type": "string", "enum": CATEGORIES}},
            "required": ["ids", "text", "category"], "additionalProperties": False}},
        "outdated": {"type": "array", "items": {
            "type": "object", "properties": {"id": {"type": "integer"}, "reason": {"type": "string"}},
            "required": ["id", "reason"], "additionalProperties": False}},
        "recategorize": {"type": "array", "items": {
            "type": "object",
            "properties": {"id": {"type": "integer"}, "category": {"type": "string", "enum": CATEGORIES}},
            "required": ["id", "category"], "additionalProperties": False}},
    },
    "required": ["merge", "outdated", "recategorize"],
    "additionalProperties": False,
}


class MemoryTidy:
    def __init__(self, path: str | os.PathLike, memory, client=None,
                 on_usage: Callable | None = None,
                 clock: Callable[[], datetime.datetime] = datetime.datetime.now) -> None:
        self.path = Path(path)
        self.memory = memory
        self.client = client
        self.on_usage = on_usage
        self.clock = clock
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        self.last_run = str(data.get("last_run", ""))
        self.proposals: list[dict] = list(data.get("proposals") or [])

    def _save(self) -> None:
        self.path.write_text(json.dumps({"last_run": self.last_run, "proposals": self.proposals},
                                        ensure_ascii=False, indent=1), encoding="utf-8")

    def due(self) -> bool:
        if self.proposals or len(self.memory.facts) < MIN_FACTS or self.client is None:
            return False
        if not self.last_run:
            return True
        last = datetime.date.fromisoformat(self.last_run)
        return (self.clock().date() - last).days >= EVERY_DAYS

    # ---------- 请 Haiku 提建议 ----------
    def run(self) -> int:
        """请 Haiku 看一遍,存下建议;返回有几条建议。"""
        import friday_security

        facts = list(self.memory.facts)
        listing = "\n".join(f"{i}. {f['text']}(记于 {f.get('added', '?')},分类:{f.get('category') or '其他'})"
                            for i, f in enumerate(facts, 1))
        response = self.client.messages.create(
            model=MODEL, max_tokens=4096,
            system=PROMPT.format(today=self.clock().strftime("%Y-%m-%d")),
            messages=[{"role": "user", "content": listing}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
        )
        if self.on_usage is not None and getattr(response, "usage", None) is not None:
            try:
                self.on_usage(response.usage, MODEL)
            except Exception:
                pass
        self.last_run = self.clock().strftime("%Y-%m-%d")
        if getattr(response, "stop_reason", None) == "refusal":
            self._save()
            return 0
        text = next(b.text for b in response.content if getattr(b, "type", "") == "text")
        data = json.loads(text)

        def fact(i):
            return facts[i - 1]["text"] if isinstance(i, int) and 1 <= i <= len(facts) else None

        proposals = []
        for item in data.get("merge") or []:
            sources = [fact(i) for i in dict.fromkeys(item.get("ids") or [])]
            if len(sources) >= 2 and all(sources) and item.get("text") \
                    and not friday_security.looks_like_instruction(item["text"]):
                proposals.append({"kind": "merge", "from": sources, "text": item["text"][:300],
                                  "category": item.get("category") or "其他"})
        for item in data.get("outdated") or []:
            if fact(item.get("id")):
                proposals.append({"kind": "outdated", "text": fact(item["id"]), "reason": str(item.get("reason", ""))[:200]})
        for item in data.get("recategorize") or []:
            old = facts[item["id"] - 1] if fact(item.get("id")) else None
            if old and item.get("category") in CATEGORIES and item["category"] != (old.get("category") or "其他"):
                proposals.append({"kind": "category", "text": old["text"], "category": item["category"]})
        for n, p in enumerate(proposals, 1):
            p["id"] = n
        self.proposals = proposals
        self._save()
        return len(proposals)

    def summary(self) -> str:
        kinds = {"merge": 0, "outdated": 0, "category": 0}
        for p in self.proposals:
            kinds[p["kind"]] += 1
        parts = [f"{label} {kinds[k]} 处" for k, label in (("merge", "合并"), ("outdated", "可能过时"), ("category", "换分类"))
                 if kinds[k]]
        return "、".join(parts)

    # ---------- 你同意了才改 ----------
    def apply(self, ids: list[int]) -> str:
        chosen = [p for p in self.proposals if p["id"] in set(ids)]
        texts = {f["text"]: f for f in self.memory.facts}
        done = 0
        for p in chosen:
            if p["kind"] == "merge":
                sources = [texts[t] for t in p["from"] if t in texts]
                if len(sources) < 2:
                    continue  # 期间已经改过了
                added = min(f.get("added", "") for f in sources)
                self.memory.facts = [f for f in self.memory.facts if f not in sources]
                self.memory.facts.append({"text": p["text"], "added": added, "category": p["category"]})
                done += 1
            elif p["kind"] == "outdated" and p["text"] in texts:
                self.memory.facts = [f for f in self.memory.facts if f["text"] != p["text"]]
                done += 1
            elif p["kind"] == "category" and p["text"] in texts:
                texts[p["text"]]["category"] = p["category"]
                done += 1
            texts = {f["text"]: f for f in self.memory.facts}
        if done:
            self.memory.save()
        self.proposals = []
        self._save()
        return f"记忆整理好了:改了 {done} 处" if done else "这次没有改记忆"
