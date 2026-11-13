"""购物清单:“要买牛奶、鸡蛋”就记上;在外面用手机问“要买什么”;“牛奶买了”就划掉,带价钱还会顺便记账(不花钱)。

存在 friday_shopping.json。
"""

from __future__ import annotations

import datetime
import json
import os
import re
import threading
from pathlib import Path
from typing import Callable

SPLIT = re.compile(r"[、,，;；/／]|(?<![A-Za-z])\s+|\s+(?![A-Za-z])|(?<=[一-鿿])(?:和|跟|还有|以及)(?=[一-鿿])")
KEEP_DONE_DAYS = 3


def split_items(text: str) -> list[str]:
    items = []
    for part in SPLIT.split(str(text or "")):
        part = part.strip(" 。.!!")
        part = re.sub(r"^(一些|一点|些|几(个|包|瓶|盒)|[一二两三四五六七八九十\d]+(个|盒|包|瓶|袋|打|斤|公斤|罐|条|块|片|箱|支|颗|粒))",
                      "", part) or part
        if part in ("和", "跟", "还有", "以及", "and", "&"):
            continue
        if part and part not in items and len(part) <= 30:
            items.append(part)
    return items


class Shopping:
    def __init__(self, path: str | os.PathLike, clock: Callable[[], datetime.datetime] = datetime.datetime.now) -> None:
        self.path = Path(path)
        self.clock = clock
        self.lock = threading.Lock()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        items = data.get("items") if isinstance(data, dict) else None
        self.items: list[dict] = [i for i in (items or []) if isinstance(i, dict) and i.get("text")]

    def _save(self) -> None:
        cutoff = (self.clock() - datetime.timedelta(days=KEEP_DONE_DAYS)).isoformat()
        self.items = [i for i in self.items if not i.get("done") or i["done"] >= cutoff]
        temp = self.path.with_name(f"{self.path.name}.{threading.get_ident()}.tmp")
        temp.write_text(json.dumps({"items": self.items}, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(temp, self.path)

    def pending(self) -> list[dict]:
        return [i for i in self.items if not i.get("done")]

    def add(self, text: str) -> str:
        new = split_items(text)
        if not new:
            raise ValueError("要买什么?")
        with self.lock:
            have = {i["text"] for i in self.pending()}
            added = [n for n in new if n not in have]
            for name in added:
                self.items.append({"text": name, "added": self.clock().isoformat(timespec="minutes")})
            self._save()
        already = [n for n in new if n not in added]
        text = ("记上了:" + "、".join(added) + "。") if added else ""
        if already:
            text += "、".join(already) + " 本来就在清单上。"
        return text + f"清单上一共 {len(self.pending())} 样。"

    def find(self, name: str) -> dict | None:
        name = name.strip()
        pending = self.pending()
        for item in pending:
            if item["text"] == name:
                return item
        hits = [i for i in pending if name in i["text"] or i["text"] in name]
        return hits[0] if len(hits) == 1 else None

    def done(self, name: str) -> tuple[str, dict] | None:
        """划掉一样;清单上没有就返回 None。"""
        with self.lock:
            item = self.find(name)
            if item is None:
                return None
            item["done"] = self.clock().isoformat(timespec="minutes")
            self._save()
        left = self.pending()
        tail = f"还要买:{'、'.join(i['text'] for i in left)}。" if left else "清单上的都买齐了!"
        return f"划掉了{item['text']}。{tail}", item

    def remove(self, name: str) -> str:
        with self.lock:
            item = self.find(name)
            if item is None:
                raise ValueError(f"清单上没有“{name}”")
            self.items.remove(item)
            self._save()
        return f"从清单拿掉了{item['text']}。"

    def clear(self) -> str:
        with self.lock:
            count = len(self.pending())
            self.items = []
            self._save()
        return f"购物清单清空了({count} 样)。"

    def describe(self) -> str:
        left = self.pending()
        if not left:
            return "购物清单是空的。说“要买牛奶、鸡蛋”就能记。"
        return f"要买 {len(left)} 样:" + "、".join(i["text"] for i in left) + "。"
