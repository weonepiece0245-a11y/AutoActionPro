"""Steam 愿望单打折提醒:愿望单里的游戏打折了就告诉你(电脑和手机都会收到),说“愿望单有什么在打折”也能查(不花钱)。

用 齿轮设定 → 连接的应用 里已经填好的 Steam 钥匙和个人资料;个人资料的“游戏详情”要公开才读得到愿望单。
价格是马来西亚区(马币),每 6 小时看一次。同一次打折只提醒一次;折扣更大了会再说。存在 friday_wishlist.json。
可以设“至少几折才提醒”(齿轮设定 → 游戏 → Steam),免得一直被小折扣吵。
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Callable

STORE = "https://store.steampowered.com/api/appdetails"
CHECK_EVERY = 6 * 3600
BATCH = 50
MAX_NAMES = 15  # 每次最多查几个新游戏的名字(打折的才查)


MIN_CHOICES = (0, 20, 30, 50, 70)


def min_discount() -> int:
    """至少打几 % 折才提醒(0 = 有折扣就提醒)。"""
    try:
        value = int(os.environ.get("FRIDAY_WISHLIST_MIN", "0"))
    except ValueError:
        return 0
    return value if value in MIN_CHOICES else 0


def enabled() -> bool:
    return os.environ.get("FRIDAY_WISHLIST", "on").strip().lower() not in ("off", "0", "false", "no")


def country() -> str:
    code = os.environ.get("FRIDAY_STEAM_CC", "my").strip().lower()
    return code if len(code) == 2 and code.isalpha() else "my"


class Wishlist:
    def __init__(self, steam, path: str | os.PathLike, fetch: Callable[[str], dict] | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        import friday_steam

        self.steam = steam
        self.path = Path(path)
        self.fetch = fetch or friday_steam.fetch_json
        self.clock = clock
        self.lock = threading.Lock()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        data = data if isinstance(data, dict) else {}
        self.names: dict[str, str] = dict(data.get("names") or {})
        self.told: dict[str, int] = {str(k): int(v) for k, v in (data.get("told") or {}).items()}
        self.checked = float(data.get("checked") or 0)
        self.sales: list[dict] = []

    def _save(self) -> None:
        if len(self.names) > 500:  # 只留最近查过的
            self.names = dict(list(self.names.items())[-500:])
        temp = self.path.with_name(f"{self.path.name}.{threading.get_ident()}.tmp")
        temp.write_text(json.dumps({"names": self.names, "told": self.told, "checked": self.checked},
                                   ensure_ascii=False), encoding="utf-8")
        os.replace(temp, self.path)

    # ---------- 读 Steam ----------
    def app_ids(self) -> list[int]:
        data = self.steam._call("IWishlistService/GetWishlist/v1/", steamid=self.steam.my_id())
        items = (data.get("response") or {}).get("items") or []
        return [int(i["appid"]) for i in sorted(items, key=lambda i: i.get("priority", 0)) if i.get("appid")]

    def prices(self, ids: list[int]) -> dict[int, dict]:
        found = {}
        for start in range(0, len(ids), BATCH):
            chunk = ids[start:start + BATCH]
            query = urllib.parse.urlencode({"appids": ",".join(map(str, chunk)), "cc": country(),
                                            "filters": "price_overview"})
            data = self.fetch(f"{STORE}?{query}")
            for appid in chunk:
                entry = data.get(str(appid)) or {}
                price = (entry.get("data") or {}).get("price_overview") if isinstance(entry.get("data"), dict) else None
                if entry.get("success") and price:
                    found[appid] = price
        return found

    def name(self, appid: int) -> str:
        key = str(appid)
        if key not in self.names:
            query = urllib.parse.urlencode({"appids": appid, "cc": country(), "l": "english"})  # 和游戏库一样用英文名
            try:
                data = (self.fetch(f"{STORE}?{query}").get(key) or {}).get("data") or {}
                self.names[key] = str(data.get("name") or f"游戏 {appid}")[:60]
            except Exception:
                return f"游戏 {appid}"
        return self.names[key]

    # ---------- 看打折 ----------
    def refresh(self) -> list[dict]:
        """看一次愿望单:[{appid, name, pct, final, initial}],打折的,折扣大的在前。"""
        ids = self.app_ids()
        if not ids:
            raise ValueError("读不到愿望单:可能是空的,或者 Steam 个人资料的“游戏详情”不是公开的。")
        prices = self.prices(ids)
        sales = []
        looked = 0
        self.unnamed = 0
        for appid in ids:
            price = prices.get(appid) or {}
            pct = int(price.get("discount_percent") or 0)
            if pct <= 0:
                continue
            if str(appid) not in self.names:
                if looked >= MAX_NAMES:
                    self.unnamed += 1  # 名字下次再查
                    continue
                looked += 1
            sales.append({"appid": appid, "name": self.name(appid), "pct": pct,
                          "final": price.get("final_formatted") or "", "initial": price.get("initial_formatted") or ""})
        sales.sort(key=lambda s: -s["pct"])
        with self.lock:
            self.sales = sales
            self.total = len(ids)
            self.checked = self.clock()
            on_sale = {str(s["appid"]) for s in sales}
            self.told = {k: v for k, v in self.told.items() if k in on_sale}  # 打完折了:下次打折再提醒
            self._save()
        return sales

    @staticmethod
    def _line(sale: dict) -> str:
        price = f" → {sale['final']}" if sale["final"] else ""
        was = f"(原价 {sale['initial']})" if sale["initial"] else ""
        return f"• {sale['name']}:-{sale['pct']}%{price}{was}"

    def check(self) -> str | None:
        """定时看:有新打折(或折扣更大了)、而且够设定的折扣,返回要说的话。"""
        sales = self.refresh()
        floor = min_discount()
        new = [s for s in sales if s["pct"] >= floor and s["pct"] > self.told.get(str(s["appid"]), 0)]
        if not new:
            return None
        with self.lock:
            for s in new:
                self.told[str(s["appid"])] = s["pct"]
            self._save()
        lines = "\n".join(self._line(s) for s in new[:5])
        more = f"\n……还有 {len(new) - 5} 个,说“愿望单打折”看全部。" if len(new) > 5 else ""
        return f"🛒 Steam 愿望单有 {len(new)} 个游戏新打折:\n{lines}{more}"

    def due(self) -> bool:
        return self.clock() - self.checked >= CHECK_EVERY

    def describe(self) -> str:
        sales = self.refresh()
        if not sales:
            return f"愿望单里 {getattr(self, 'total', 0)} 个游戏,现在都没打折。有打折我会告诉你。"
        lines = "\n".join(self._line(s) for s in sales[:12])
        more = f"\n还有 {len(sales) - 12} 个。" if len(sales) > 12 else ""
        later = f"\n(另外 {self.unnamed} 个也在打折,名字下次再查)" if getattr(self, "unnamed", 0) else ""
        return f"愿望单 {getattr(self, 'total', 0)} 个游戏里,在打折的有 {len(sales)} 个(折扣大的在前):\n{lines}{more}{later}"

    def state(self) -> dict:
        return {"on": enabled(), "min": min_discount(), "total": getattr(self, "total", 0),
                "sales": len(self.sales), "checked": self.checked}
