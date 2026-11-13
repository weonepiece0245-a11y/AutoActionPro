"""新闻简报:马来西亚、国际、科技、财经、体育、娱乐的头条(Google 新闻的公开 RSS,不花钱)。

头条直接读出来不问 Claude;想知道某一条的详情,Claude 会去搜那条新闻再讲(会花一点点)。
新闻标题是别人写的,只当资料。
"""

from __future__ import annotations

import email.utils
import html
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Callable

CACHE_SECONDS = 20 * 60
BASE = "https://news.google.com/rss"
ZH = "hl=zh-CN&gl=CN&ceid=CN:zh-Hans"
EN_MY = "hl=en-MY&gl=MY&ceid=MY:en"
TOPICS = {  # 名字: (说法, 先试的网址们)
    "malaysia": ("马来西亚", [f"{BASE}/search?q={urllib.parse.quote('马来西亚 when:1d')}&{ZH}", f"{BASE}?{EN_MY}"]),
    "world": ("国际", [f"{BASE}/headlines/section/topic/WORLD?{ZH}", f"{BASE}/headlines/section/topic/WORLD?{EN_MY}"]),
    "tech": ("科技", [f"{BASE}/headlines/section/topic/TECHNOLOGY?{ZH}", f"{BASE}/headlines/section/topic/TECHNOLOGY?{EN_MY}"]),
    "business": ("财经", [f"{BASE}/headlines/section/topic/BUSINESS?{ZH}", f"{BASE}/headlines/section/topic/BUSINESS?{EN_MY}"]),
    "sports": ("体育", [f"{BASE}/headlines/section/topic/SPORTS?{ZH}", f"{BASE}/headlines/section/topic/SPORTS?{EN_MY}"]),
    "entertainment": ("娱乐", [f"{BASE}/headlines/section/topic/ENTERTAINMENT?{ZH}",
                              f"{BASE}/headlines/section/topic/ENTERTAINMENT?{EN_MY}"]),
}
WORDS = {"马来西亚": "malaysia", "大马": "malaysia", "本地": "malaysia", "国内": "malaysia", "国际": "world",
         "世界": "world", "国外": "world", "科技": "tech", "财经": "business", "经济": "business", "股市": "business",
         "体育": "sports", "娱乐": "entertainment"}


def fetch_text(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (friday-assistant)"})
    with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310
        return response.read(2_000_000).decode("utf-8", errors="replace")


def parse_rss(text: str) -> list[dict]:
    root = ET.fromstring(text)
    items = []
    for item in root.iter("item"):
        title = html.unescape((item.findtext("title") or "").strip())
        source = (item.findtext("source") or "").strip()
        if source and title.endswith(" - " + source):
            title = title[: -len(source) - 3].strip()
        elif not source and " - " in title:
            title, source = title.rsplit(" - ", 1)
        stamp = 0.0
        try:
            stamp = email.utils.parsedate_to_datetime(item.findtext("pubDate") or "").timestamp()
        except (TypeError, ValueError):
            pass
        if title:
            items.append({"title": title, "source": source, "link": (item.findtext("link") or "").strip(), "time": stamp})
    return items


def search_url(query: str) -> str:
    return f"{BASE}/search?q={urllib.parse.quote(query + ' when:3d')}&{ZH}"


class News:
    def __init__(self, fetch: Callable[[str], str] = fetch_text, clock: Callable[[], float] = time.time) -> None:
        self.fetch = fetch
        self.clock = clock
        self.cache: dict[str, tuple[float, list[dict]]] = {}
        self.last: list[dict] = []  # 上次读出来的(带编号,问详情用)

    def _load(self, urls: list[str]) -> list[dict]:
        key = urls[0]
        hit = self.cache.get(key)
        if hit and self.clock() - hit[0] < CACHE_SECONDS:
            return hit[1]
        error = None
        for url in urls:
            try:
                items = parse_rss(self.fetch(url))
            except Exception as e:  # noqa: BLE001
                error = e
                continue
            if items:
                seen, unique = set(), []
                for item in sorted(items, key=lambda i: -i["time"]):
                    short = re.sub(r"\W", "", item["title"])[:18]
                    if short not in seen:
                        seen.add(short)
                        unique.append(item)
                self.cache[key] = (self.clock(), unique)
                return unique
        raise ValueError("现在拿不到新闻(网络有问题),等一下再试") from error

    def headlines(self, topic: str = "malaysia", count: int = 5) -> list[dict]:
        return self._load(TOPICS[topic][1])[:count]

    def search(self, query: str, count: int = 6) -> list[dict]:
        return self._load([search_url(query)])[:count]

    def describe(self, groups: list[tuple[str, list[dict]]]) -> str:
        self.last = []
        parts = []
        for label, items in groups:
            if not items:
                continue
            lines = []
            for item in items:
                self.last.append(item)
                source = f"({item['source']})" if item["source"] else ""
                lines.append(f"{len(self.last)}. {item['title']}{source}")
            parts.append(f"{label}:\n" + "\n".join(lines))
        if not parts:
            return "现在没有找到新闻。"
        return "\n".join(parts) + "\n想知道哪一条的详情,说“第几条详细说说”。"

    def today(self) -> str:
        """平常问“今天有什么新闻”:马来西亚 5 条 + 国际 3 条。"""
        groups = []
        for topic, count in (("malaysia", 5), ("world", 3)):
            try:
                groups.append((TOPICS[topic][0] + "新闻", self.headlines(topic, count)))
            except ValueError:
                continue
        if not groups:
            raise ValueError("现在拿不到新闻(网络有问题),等一下再试")
        return self.describe(groups)

    def topic(self, topic: str, count: int = 6) -> str:
        return self.describe([(TOPICS[topic][0] + "新闻", self.headlines(topic, count))])

    def about(self, query: str) -> str:
        return self.describe([(f"关于“{query}”的新闻", self.search(query))])

    def item(self, number: int) -> dict:
        if not 1 <= number <= len(self.last):
            raise ValueError("没有这一条,先说“今天有什么新闻”")
        return self.last[number - 1]

    def brief_line(self, count: int = 3) -> str | None:
        items = self.headlines("malaysia", count)
        if not items:
            return None
        return "新闻:" + ";".join(i["title"] for i in items) + "。"


def topic_of(word: str) -> str | None:
    return WORDS.get(word)
