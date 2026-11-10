"""重要邮件提醒:你设定的人或关键词的新邮件一到,马上在电脑上说、推到手机。

每 2 分钟问一次 Gmail 有没有新的未读邮件,只看寄件人和标题(不问 Claude,不花钱)。
规则和看过哪些邮件存在 friday_mailwatch.json。
"""

from __future__ import annotations

import html
import json
import os
import re
from pathlib import Path
from typing import Callable

CHECK_EVERY = 2 * 60
MAX_RULES = 30
SEEN_KEEP = 300


def _clean(items) -> list[str]:
    out = []
    for item in items:
        item = " ".join(str(item).split()).strip(",,;;")
        if item and item.lower() not in [o.lower() for o in out]:
            out.append(item[:80])
    return out[:MAX_RULES]


def _sender_name(raw: str) -> str:
    """'王老板 <boss@x.com>' -> '王老板';没有名字就用邮箱。"""
    match = re.match(r'\s*"?([^"<]*?)"?\s*<([^>]+)>', raw or "")
    if match:
        return match.group(1).strip() or match.group(2)
    return (raw or "").strip()


class MailWatch:
    def __init__(self, path: str | os.PathLike) -> None:
        self.path = Path(path)
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        data = data if isinstance(data, dict) else {}
        self.senders: list[str] = _clean(data.get("senders") or [])
        self.words: list[str] = _clean(data.get("words") or [])
        self.important = bool(data.get("important", False))
        self.seen: list[str] = [str(i) for i in (data.get("seen") or [])][-SEEN_KEEP:]
        self.primed = bool(data.get("primed", False))  # 第一次看时,已经在信箱里的旧邮件不算新邮件

    def _save(self) -> None:
        data = {"senders": self.senders, "words": self.words, "important": self.important,
                "seen": self.seen[-SEEN_KEEP:], "primed": self.primed}
        temp = self.path.with_name(self.path.name + ".tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(temp, self.path)

    def active(self) -> bool:
        return bool(self.senders or self.words or self.important)

    # ---------- 规则 ----------
    def set_rules(self, senders=None, words=None, important=None) -> None:
        if senders is not None:
            self.senders = _clean(senders)
        if words is not None:
            self.words = _clean(words)
        if important is not None:
            self.important = bool(important)
        self._save()

    def add(self, sender: str = "", word: str = "") -> str:
        if not sender and not word:
            raise ValueError("要给寄件人(名字或邮箱)或关键词")
        if sender:
            self.senders = _clean(self.senders + [sender])
        if word:
            self.words = _clean(self.words + [word])
        self._save()
        return "好的,以后这类邮件一到就告诉你。" + self.describe()

    def remove(self, key: str) -> str:
        key = key.strip().lower()
        before = len(self.senders) + len(self.words)
        self.senders = [s for s in self.senders if s.lower() != key]
        self.words = [w for w in self.words if w.lower() != key]
        if len(self.senders) + len(self.words) == before:
            raise ValueError(f"规则里没有“{key}”")
        self._save()
        return "已删掉。" + self.describe()

    def describe(self) -> str:
        parts = []
        if self.senders:
            parts.append("寄件人:" + "、".join(self.senders))
        if self.words:
            parts.append("标题关键词:" + "、".join(self.words))
        if self.important:
            parts.append("Gmail 标为重要的邮件")
        return ("现在会提醒的邮件 —— " + ";".join(parts)) if parts else "现在没有设定要提醒的邮件"

    def matches(self, sender: str, subject: str, labels: list[str]) -> str | None:
        """对得上就返回原因(给提醒用),对不上返回 None。"""
        low_sender, low_subject = sender.lower(), subject.lower()
        for rule in self.senders:
            if rule.lower() in low_sender:
                return f"来自 {rule}"
        for word in self.words:
            if word.lower() in low_subject:
                return f"标题有“{word}”"
        if self.important and "IMPORTANT" in labels:
            return "Gmail 标为重要"
        return None

    # ---------- 检查 ----------
    def check(self, list_unread: Callable[[], list[str]], fetch: Callable[[str], dict]) -> list[str]:
        """看看有没有新的未读邮件对得上规则;返回要说的话。

        list_unread() -> 未读邮件 id;fetch(id) -> {"from", "subject", "snippet", "labels"}。
        """
        if not self.active():
            return []
        ids = list_unread()
        new = [i for i in ids if i not in self.seen]
        if not self.primed:  # 刚开启:信箱里原来的邮件不算
            self.seen = (self.seen + new)[-SEEN_KEEP:]
            self.primed = True
            self._save()
            return []
        said = []
        for mail_id in reversed(new):  # 旧的先说
            self.seen.append(mail_id)
            try:
                mail = fetch(mail_id)
            except Exception:
                continue
            reason = self.matches(mail.get("from", ""), mail.get("subject", ""), mail.get("labels") or [])
            if reason:
                snippet = " ".join(html.unescape(mail.get("snippet", "")).split())[:60]
                text = f"新邮件({reason}):{_sender_name(mail.get('from', ''))}「{mail.get('subject') or '(无主题)'}」"
                said.append(text + (f" —— {snippet}……" if snippet else ""))
        if new:
            self.seen = self.seen[-SEEN_KEEP:]
            self._save()
        return said


def gmail_source(google) -> tuple[Callable[[], list[str]], Callable[[str], dict]]:
    """用星期五的 Google 登录读 Gmail(只读寄件人、标题、摘要)。"""
    import friday_google

    def list_unread() -> list[str]:
        api = google.account.service("gmail").users().messages()
        result = api.list(userId="me", q="in:inbox is:unread newer_than:2d", maxResults=30).execute()
        return [m["id"] for m in result.get("messages", [])]

    def fetch(mail_id: str) -> dict:
        api = google.account.service("gmail").users().messages()
        msg = api.get(userId="me", id=mail_id, format="metadata", metadataHeaders=["From", "Subject"]).execute()
        return {"from": friday_google._header(msg, "From"), "subject": friday_google._header(msg, "Subject"),
                "snippet": msg.get("snippet", ""), "labels": msg.get("labelIds", [])}

    return list_unread, fetch
