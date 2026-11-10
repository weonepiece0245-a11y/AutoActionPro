"""剪贴板历史:记住最近复制过的 50 段文字,说“剪贴板历史”就列出来,“复制第 3 个”就放回剪贴板(不花钱)。

只记文字;看起来像密码、钥匙、验证码的不记,密码管理器复制的(它们会标“不要记录”)也不记。
只存在这台电脑的 friday_clipboard.json,不备份到 Google Drive。可以在齿轮设定里关掉或清空。
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Callable

MAX_ITEMS = 50
MAX_CHARS = 5000
POLL_SECONDS = 1.0
SKIP_FORMATS = ("ExcludeClipboardContentFromMonitorProcessing", "CanIncludeInClipboardHistory",
                "Clipboard Viewer Ignore")  # 密码管理器复制密码时会带这些标记

KEY_RE = re.compile(
    r"(sk-ant-|sk-[a-z0-9]{16,}|ghp_|gho_|ghs_|github_pat_|xox[abpr]-|AKIA[0-9A-Z]{12,}|AIza[0-9A-Za-z_-]{20,}|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY|eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.|"
    r"\b[A-Za-z0-9_-]{24,28}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{27,40}\b|"  # Discord 机器人钥匙
    r"(password|passwd|pwd|密码|口令|api[_ ]?key|secret|token)\s*[:=:]\s*\S)", re.I)


def enabled() -> bool:
    return os.environ.get("FRIDAY_CLIPBOARD", "on") != "off"


def looks_secret(text: str) -> bool:
    """像密码、钥匙、验证码的就不记。"""
    text = text.strip()
    if KEY_RE.search(text):
        return True
    if re.fullmatch(r"\d{4,8}", text):
        return True  # 验证码
    if re.search(r"\s", text) or not 8 <= len(text) <= 64:
        return False
    if "://" in text or text.lower().startswith("www.") or re.search(r"[\\/]", text) or re.fullmatch(r"[^@\s]+@[^@\s]+\.\w+", text):
        return False  # 网址、文件路径、邮箱
    kinds = sum(bool(re.search(p, text)) for p in (r"[a-z]", r"[A-Z]", r"\d", r"[^A-Za-z0-9]"))
    return kinds >= 3 and not re.search(r"[一-鿿]", text)  # 一串不带空格、又有大小写数字符号的:像密码


def _windows_reader() -> tuple[Callable[[], int], Callable[[], bool], Callable[[], str | None]]:
    """(剪贴板序号, 这次要不要跳过, 读文字)。"""
    import ctypes

    import friday_desktop

    user32 = ctypes.windll.user32
    user32.RegisterClipboardFormatW.restype = ctypes.c_uint
    formats = [user32.RegisterClipboardFormatW(name) for name in SKIP_FORMATS]

    def skip() -> bool:
        return any(f and user32.IsClipboardFormatAvailable(f) for f in formats)

    return user32.GetClipboardSequenceNumber, skip, friday_desktop.read_clipboard


class ClipHistory:
    def __init__(self, path: str | os.PathLike, clock: Callable[[], float] = time.time) -> None:
        self.path = Path(path)
        self.clock = clock
        self.lock = threading.Lock()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        items = data.get("items") if isinstance(data, dict) else None
        self.items: list[dict] = [i for i in (items or []) if isinstance(i, dict) and i.get("text")]  # 新的在前
        self.ignore_next: str | None = None  # 星期五自己放回剪贴板的,不当成新复制的

    def _save(self) -> None:
        try:
            temp = self.path.with_name(self.path.name + ".tmp")
            temp.write_text(json.dumps({"items": self.items}, ensure_ascii=False), encoding="utf-8")
            os.replace(temp, self.path)
        except OSError:
            pass

    def add(self, text: str | None) -> bool:
        """记一段新复制的文字;不该记的返回 False。"""
        if not text or not text.strip() or not enabled():
            return False
        text = text[:MAX_CHARS]
        if looks_secret(text):
            return False
        with self.lock:
            if self.ignore_next is not None and text == self.ignore_next:
                self.ignore_next = None
                return False
            self.items = [i for i in self.items if i["text"] != text]
            self.items.insert(0, {"text": text, "time": self.clock()})
            del self.items[MAX_ITEMS:]
            self._save()
        return True

    def clear(self) -> str:
        with self.lock:
            count = len(self.items)
            self.items = []
            self._save()
        return f"剪贴板历史清空了({count} 段)。"

    def get(self, number: int) -> dict:
        if not 1 <= number <= len(self.items):
            raise ValueError(f"没有第 {number} 个,现在记着 {len(self.items)} 段")
        return self.items[number - 1]

    def find(self, query: str = "", kind: str = "") -> list[tuple[int, dict]]:
        query = query.strip().lower()
        found = []
        for n, item in enumerate(self.items, 1):
            text = item["text"]
            if kind == "url" and not re.search(r"https?://|www\.", text):
                continue
            if kind == "number" and not re.search(r"\d{3,}", text):
                continue
            if kind == "email" and not re.search(r"[^@\s]+@[^@\s]+\.\w+", text):
                continue
            if query and query not in text.lower():
                continue
            found.append((n, item))
        return found

    @staticmethod
    def preview(text: str, width: int = 60) -> str:
        one = " ".join(text.split())
        return one if len(one) <= width else one[:width] + "…"

    def describe(self, found: list[tuple[int, dict]] | None = None, limit: int = 10) -> str:
        found = list(enumerate(self.items, 1)) if found is None else found
        if not found:
            return "剪贴板历史是空的。" if not self.items else "没找到这样的。"
        lines = [f"#{n} {self.preview(item['text'])}" for n, item in found[:limit]]
        more = f"\n(还有 {len(found) - limit} 段,说“剪贴板里找 关键词”可以找)" if len(found) > limit else ""
        return "最近复制的(说“复制第几个”就放回剪贴板):\n" + "\n".join(lines) + more

    def copy_back(self, number: int, write: Callable[[str], bool]) -> str:
        item = self.get(number)
        with self.lock:
            self.ignore_next = item["text"]
        if not write(item["text"]):
            raise ValueError("放不进剪贴板,等一下再试")
        with self.lock:  # 放回去的移到最前面
            self.items = [i for i in self.items if i["text"] != item["text"]]
            self.items.insert(0, {"text": item["text"], "time": self.clock()})
            self._save()
        return f"已放回剪贴板,可以 Ctrl+V 贴上:{self.preview(item['text'], 40)}"

    def start(self, reader=None, interval: float = POLL_SECONDS) -> bool:
        """在后台看剪贴板有没有变(只在 Windows 上)。"""
        if reader is None:
            if os.name != "nt":
                return False
            reader = _windows_reader()
        sequence, skip, read = reader

        def loop() -> None:
            last = sequence()
            while True:
                time.sleep(interval)
                try:
                    now = sequence()
                    if now == last:
                        continue
                    last = now
                    if not enabled() or skip():
                        continue
                    self.add(read())
                except Exception:
                    pass

        threading.Thread(target=loop, daemon=True, name="friday-clipboard").start()
        return True
