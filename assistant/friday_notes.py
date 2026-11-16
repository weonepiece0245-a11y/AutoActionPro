"""语音速记本:说“记一下:……”就存下来(在电脑上直接存,不问 Claude,不花钱),之后能找、能删。

笔记存在 friday_notes.json;登录了 Google 的话,同时抄一份到 Google 文档“星期五笔记”,手机上也看得到。
"""

from __future__ import annotations

import datetime
import json
import os
import re
from pathlib import Path
from typing import Callable

MAX_NOTE = 2000
DOC_TITLE = "星期五笔记"

Clock = Callable[[], datetime.datetime]


class Notes:
    def __init__(self, path: str | os.PathLike, clock: Clock = datetime.datetime.now) -> None:
        self.path = Path(path)
        self.clock = clock
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        data = data if isinstance(data, dict) else {}
        self.items: list[dict] = [n for n in (data.get("notes") or []) if isinstance(n, dict) and n.get("text")]
        self.doc_id = str(data.get("doc_id", ""))
        self.sync: Callable[[str], None] | None = None  # 抄到 Google 文档(由主程序接上)

    def _save(self) -> None:
        temp = self.path.with_name(self.path.name + ".tmp")
        temp.write_text(json.dumps({"notes": self.items, "doc_id": self.doc_id}, ensure_ascii=False, indent=1),
                        encoding="utf-8")
        os.replace(temp, self.path)

    def add(self, text: str) -> str:
        text = str(text or "").strip().lstrip(":: ,,").strip()
        if not text:
            raise ValueError("要记什么?")
        number = max((n["id"] for n in self.items), default=0) + 1
        note = {"id": number, "time": self.clock().strftime("%Y-%m-%d %H:%M"), "text": text[:MAX_NOTE]}
        self.items.append(note)
        self._save()
        if self.sync is not None:
            try:
                self.sync(f"[{note['time']}] {note['text']}")
            except Exception:
                pass  # 抄不过去也没关系,电脑上记好了
        return f"记好了(#{number})。"

    def delete(self, key: str = "last") -> str:
        if not self.items:
            raise ValueError("笔记本是空的")
        key = str(key).strip().lstrip("#")
        if key in ("", "last", "最后", "刚才"):
            note = self.items[-1]
        else:
            note = next((n for n in self.items if str(n["id"]) == key), None)
            if note is None:
                raise ValueError(f"没有 #{key} 这条笔记")
        self.items.remove(note)
        self._save()
        return f"删掉了 #{note['id']}:{note['text'][:40]}"

    def search(self, query: str = "", days: int = 0, limit: int = 10) -> list[dict]:
        words = [w.lower() for w in re.split(r"[\s,,、]+", str(query or "")) if w]
        cutoff = (self.clock() - datetime.timedelta(days=days)).strftime("%Y-%m-%d") if days else ""
        found = [n for n in self.items if n["time"][:10] >= cutoff and all(w in n["text"].lower() for w in words)]
        return list(reversed(found))[:limit]

    @staticmethod
    def describe(notes: list[dict], empty: str = "没有找到笔记") -> str:
        if not notes:
            return empty
        return "\n".join(f"#{n['id']} {n['time'][5:]} {n['text']}" for n in notes)


def docs_sync(google, notes: Notes) -> Callable[[str], None]:
    """把新笔记接到 Google 文档“星期五笔记”的最后(没有就新建一个)。"""

    def append(line: str) -> None:
        api = google.account.service("docs").documents()
        if notes.doc_id:
            try:
                doc = api.get(documentId=notes.doc_id).execute()
            except Exception as e:
                if "404" not in str(e):
                    raise
                notes.doc_id = ""
        if not notes.doc_id:
            notes.doc_id = api.create(body={"title": DOC_TITLE}).execute()["documentId"]
            notes._save()
            doc = api.get(documentId=notes.doc_id).execute()
        end = max(1, doc["body"]["content"][-1]["endIndex"] - 1)
        api.batchUpdate(documentId=notes.doc_id, body={"requests": [
            {"insertText": {"location": {"index": end}, "text": line + "\n"}}]}).execute()

    return append
