"""特价和网页追踪:让星期五盯着 Steam 游戏的价格,或者某个网页,降价或有变化时告诉你。

后台每 2 小时看一次,不问 Claude,不花钱。追踪清单存在 friday_watch.json。
注意:Shopee、Lazada 这类要在浏览器里跑程序才显示内容的网站,直接读网页看不到价格,只能用 Steam 或普通网页。
"""

from __future__ import annotations

import datetime
import hashlib
import html
import ipaddress
import json
import os
import re
import socket
import urllib.parse
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

CHECK_EVERY = 2 * 60 * 60  # 每样东西隔这么久看一次
MAX_WATCHES = 20
MAX_PAGE = 2 * 1024 * 1024
STEAM_SEARCH = "https://store.steampowered.com/api/storesearch/?term={term}&l={lang}&cc=MY"
STEAM_DETAILS = "https://store.steampowered.com/api/appdetails?appids={appid}&cc=my&filters=price_overview,basic"
STEAM_URL = re.compile(r"store\.steampowered\.com/app/(\d+)")

Fetch = Callable[[str], bytes]


def _public_host(url: str) -> bool:
    """只读公开网站,不碰家里的路由器、电脑本机这些内部地址。"""
    host = urllib.parse.urlparse(url).hostname or ""
    if not host or host == "localhost" or host.endswith(".local"):
        return False
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(host, None)}
    except OSError:
        return False
    for address in addresses:
        ip = ipaddress.ip_address(address.split("%")[0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return False
    return True


class _PublicRedirects(urllib.request.HTTPRedirectHandler):
    """网站跳转时也检查:不让公开网站把请求跳到家里的路由器、电脑本机这些内部地址。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not newurl.lower().startswith(("http://", "https://")) or not _public_host(newurl):
            raise urllib.error.HTTPError(newurl, code, "跳转到了不是公开网站的地址,已拦下", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


SAFE_OPENER = urllib.request.build_opener(_PublicRedirects)


def open_public(url: str, timeout: float = 20, headers: dict | None = None):
    """打开公开网站(跳转也检查);不是公开网站抛 ValueError。"""
    if not url.lower().startswith(("http://", "https://")) or not _public_host(url):
        raise ValueError("只能打开公开网站(http / https)")
    request = urllib.request.Request(url, headers=headers or {"User-Agent": "Mozilla/5.0 (friday-assistant)",
                                                              "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"})
    return SAFE_OPENER.open(request, timeout=timeout)


def fetch(url: str) -> bytes:
    if not url.lower().startswith(("http://", "https://")) or not _public_host(url):
        raise ValueError("只能追踪公开网站(http / https)")
    with open_public(url) as response:
        return response.read(MAX_PAGE)


def page_text(raw: bytes) -> str:
    text = raw.decode("utf-8", errors="replace")
    text = re.sub(r"(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", text)
    text = re.sub(r"(?s)<!--.*?-->", " ", text)
    text = re.sub(r"(?i)<br\s*/?>|</(p|div|li|tr|h\d)>", "\n", text)
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def money(cents: int) -> str:
    return f"RM {cents / 100:,.2f}"


# ======================== Steam ========================


def steam_find(name_or_url: str, fetcher: Fetch = fetch) -> tuple[int, str]:
    found = STEAM_URL.search(name_or_url)
    if found:
        return int(found.group(1)), ""
    term = urllib.parse.quote(name_or_url)
    for lang in ("english", "schinese", "tchinese"):  # 英文名找不到就用简体、繁体中文名再找
        items = json.loads(fetcher(STEAM_SEARCH.format(term=term, lang=lang))).get("items") or []
        if items:
            return int(items[0]["id"]), items[0].get("name", "")
    raise ValueError(f"Steam 上找不到“{name_or_url}”:可以先用 web_search 查它的英文名或 Steam 商店链接再试")


def steam_price(appid: int, fetcher: Fetch = fetch) -> dict:
    """{name, final, initial, discount, free}(价钱以“分”为单位,马币)。"""
    data = json.loads(fetcher(STEAM_DETAILS.format(appid=appid)))
    entry = data.get(str(appid)) or {}
    if not entry.get("success"):
        raise ValueError("Steam 没有这个游戏的资料")
    info = entry.get("data") or {}
    price = info.get("price_overview")
    if not price:
        return {"name": info.get("name", ""), "final": 0, "initial": 0, "discount": 0, "free": True}
    return {"name": info.get("name", ""), "final": int(price["final"]), "initial": int(price["initial"]),
            "discount": int(price.get("discount_percent") or 0), "free": False}


# ======================== 清单 ========================


class Watchlist:
    def __init__(self, path: str | os.PathLike, fetcher: Fetch = fetch,
                 clock: Callable[[], datetime.datetime] = datetime.datetime.now) -> None:
        self.path = Path(path)
        self.fetcher = fetcher
        self.clock = clock
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        self.items: list[dict] = [i for i in (data.get("items") or []) if isinstance(i, dict)]

    def _save(self) -> None:
        temp = self.path.with_name(self.path.name + ".tmp")
        temp.write_text(json.dumps({"items": self.items}, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(temp, self.path)

    def _new_id(self) -> int:
        return max((i["id"] for i in self.items), default=0) + 1

    def _room(self) -> None:
        if len(self.items) >= MAX_WATCHES:
            raise ValueError(f"最多追踪 {MAX_WATCHES} 样,先删掉一些")

    def add_steam(self, name_or_url: str, target: float | None = None) -> str:
        self._room()
        appid, _ = steam_find(name_or_url, self.fetcher)
        if any(i.get("appid") == appid for i in self.items):
            raise ValueError("这个游戏已经在追踪了")
        price = steam_price(appid, self.fetcher)
        item = {"id": self._new_id(), "kind": "steam", "appid": appid, "name": price["name"] or name_or_url,
                "target": int(round(target * 100)) if target else None, "last": price,
                "checked": self.clock().timestamp(), "added": self.clock().strftime("%Y-%m-%d")}
        self.items.append(item)
        self._save()
        now = "免费游戏" if price["free"] else (money(price["final"]) + (f"(打 {100 - price['discount']} 折,原价 "
                                                                         f"{money(price['initial'])})" if price["discount"] else ""))
        want = f",降到 {money(item['target'])} 以下时告诉你" if item["target"] else ",降价或打折时告诉你"
        return f"开始追踪 #{item['id']} {item['name']}:现在 {now}{want}。每 2 小时看一次。"

    def add_page(self, url: str, keyword: str = "", name: str = "") -> str:
        self._room()
        if not url.lower().startswith(("http://", "https://")):
            raise ValueError("网址要以 http:// 或 https:// 开头")
        if STEAM_URL.search(url):
            return self.add_steam(url)
        text = page_text(self.fetcher(url))
        if len(text) < 50:
            raise ValueError("这个网页读出来几乎是空的(可能要在浏览器里跑程序才显示,比如 Shopee、Lazada),没办法追踪")
        item = {"id": self._new_id(), "kind": "page", "url": url, "name": name or urllib.parse.urlparse(url).hostname,
                "keyword": keyword.strip(), "hash": hashlib.sha256(text.encode()).hexdigest(),
                "has_keyword": bool(keyword.strip()) and keyword.strip().lower() in text.lower(),
                "lines": text.splitlines()[:400], "checked": self.clock().timestamp(),
                "added": self.clock().strftime("%Y-%m-%d"), "notified": ""}
        self.items.append(item)
        self._save()
        if item["keyword"]:
            state = "现在页面上有" if item["has_keyword"] else "现在页面上没有"
            return f"开始追踪 #{item['id']} {item['name']}:{state}“{item['keyword']}”,出现或消失时告诉你。"
        return f"开始追踪 #{item['id']} {item['name']}:网页内容有变化时告诉你(一天最多一次)。"

    def remove(self, key: str) -> str:
        key = str(key).strip().lstrip("#")
        found = [i for i in self.items if str(i["id"]) == key or (key and key.lower() in i["name"].lower())]
        if not found:
            raise ValueError(f"清单里没有“{key}”")
        self.items = [i for i in self.items if i not in found]
        self._save()
        return "不再追踪:" + "、".join(i["name"] for i in found)

    def describe(self) -> str:
        if not self.items:
            return "现在没有在追踪任何东西"
        lines = []
        for i in self.items:
            if i["kind"] == "steam":
                last = i.get("last") or {}
                now = "免费" if last.get("free") else money(last.get("final", 0))
                if last.get("discount"):
                    now += f"(-{last['discount']}%)"
                target = f",目标 {money(i['target'])}" if i.get("target") else ""
                lines.append(f"#{i['id']} Steam:{i['name']} 现在 {now}{target}")
            else:
                what = f"关键词“{i['keyword']}”" if i.get("keyword") else "内容变化"
                lines.append(f"#{i['id']} 网页:{i['name']}({what}){i['url']}")
        return "正在追踪:\n" + "\n".join(lines)

    # ---------- 后台检查 ----------
    def check(self, force: bool = False) -> list[str]:
        """看一遍到时间的项目;返回要告诉你的话。"""
        now = self.clock()
        said = []
        changed = False
        for item in self.items:
            if not force and now.timestamp() - item.get("checked", 0) < CHECK_EVERY:
                continue
            try:
                message = self._check_steam(item) if item["kind"] == "steam" else self._check_page(item, now)
            except Exception:
                continue  # 网站暂时打不开:下次再看
            item["checked"] = now.timestamp()
            changed = True
            if message:
                said.append(message)
        if changed:
            self._save()
        return said

    def _check_steam(self, item: dict) -> str | None:
        price = steam_price(item["appid"], self.fetcher)
        last = item.get("last") or {}
        item["last"] = price
        dropped = not price["free"] and last.get("final") is not None and price["final"] < last["final"]
        sale_started = bool(price["discount"]) and not last.get("discount")
        if not (dropped or sale_started):
            return None  # 没降价,也不是刚开始打折
        if item.get("target") and price["final"] > item["target"]:
            return None
        text = f"Steam 特价:{item['name']} 现在 {money(price['final'])}"
        if price["discount"]:
            text += f"(-{price['discount']}%,原价 {money(price['initial'])})"
        if last.get("final") and last["final"] != price["final"]:
            text += f",之前 {money(last['final'])}"
        return text + f"。https://store.steampowered.com/app/{item['appid']}/"

    def _check_page(self, item: dict, now: datetime.datetime) -> str | None:
        text = page_text(self.fetcher(item["url"]))
        if item.get("keyword"):
            has = item["keyword"].lower() in text.lower()
            was = item.get("has_keyword")
            item["has_keyword"] = has
            if has == was:
                return None
            return (f"网页追踪:{item['name']} 上{'出现了' if has else '没有了'}“{item['keyword']}”。{item['url']}")
        digest = hashlib.sha256(text.encode()).hexdigest()
        if digest == item.get("hash"):
            return None
        old = set(item.get("lines") or [])
        new_lines = [line for line in text.splitlines() if line not in old][:3]
        item["hash"], item["lines"] = digest, text.splitlines()[:400]
        today = now.strftime("%Y-%m-%d")
        if item.get("notified") == today:
            return None  # 一天最多说一次(很多网页一直在小改)
        item["notified"] = today
        sample = ";".join(line[:60] for line in new_lines)
        return f"网页追踪:{item['name']} 有变化" + (f",新内容像是:{sample}" if sample else "") + f"。{item['url']}"
