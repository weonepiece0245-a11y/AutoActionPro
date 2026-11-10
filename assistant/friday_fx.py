"""汇率:换算外币和马币,设“新币到 3.5 告诉我”,到了就提醒并推到手机(不花钱)。

汇率来自免费的公开数据(open.er-api.com,备用 Frankfurter/欧洲央行),每天更新一次,
是参考价,和银行、换钱店的买卖价会有一点差别。提醒存在 friday_fx.json。
"""

from __future__ import annotations

import datetime
import json
import os
import re
import time
import urllib.request
from pathlib import Path
from typing import Callable

SOURCES = ("https://open.er-api.com/v6/latest/USD", "https://api.frankfurter.app/latest?from=USD")
CACHE_SECONDS = 3600
CURRENCIES = {  # 代码: 叫法(第一个是平常说的名字)
    "MYR": ["马币", "令吉", "林吉特", "ringgit", "myr", "rm"],
    "USD": ["美金", "美元", "usd", "us$"],
    "SGD": ["新币", "新加坡元", "新元", "坡币", "sgd"],
    "CNY": ["人民币", "rmb", "cny"],
    "HKD": ["港币", "港元", "hkd"],
    "TWD": ["台币", "新台币", "twd", "ntd"],
    "JPY": ["日元", "日币", "日圆", "jpy"],
    "KRW": ["韩元", "韩币", "krw"],
    "EUR": ["欧元", "eur"],
    "GBP": ["英镑", "gbp"],
    "AUD": ["澳币", "澳元", "aud"],
    "NZD": ["纽币", "新西兰元", "nzd"],
    "CAD": ["加币", "加元", "cad"],
    "CHF": ["瑞士法郎", "chf"],
    "THB": ["泰铢", "thb"],
    "IDR": ["印尼盾", "印尼币", "idr"],
    "PHP": ["菲律宾比索", "比索", "php"],
    "VND": ["越南盾", "vnd"],
    "INR": ["印度卢比", "卢比", "inr"],
    "BND": ["文莱元", "文莱币", "bnd"],
}
NO_DECIMALS = {"JPY", "KRW", "IDR", "VND"}


def name(code: str) -> str:
    return CURRENCIES.get(code, [code])[0]


def mentions(text: str) -> list[str]:
    """句子里按出现顺序提到的货币代码。"""
    low = text.lower()
    found: list[tuple[int, str]] = []
    for code, words in CURRENCIES.items():
        for word in sorted(words, key=len, reverse=True):
            if word in ("rm",):
                hit = re.search(r"(?<![a-z])rm(?![a-z])", low)
                at = hit.start() if hit else -1
            else:
                at = low.find(word)
            if at >= 0:
                found.append((at, code))
                break
    found.sort()
    codes: list[str] = []
    for _, code in found:
        if code not in codes:
            codes.append(code)
    return codes


def money(amount: float, code: str, rate: bool = False) -> str:
    """rate:这是汇率(1 块换多少),小的数字多留几位小数。"""
    digits = 0 if code in NO_DECIMALS and not rate else 2
    if rate and abs(amount) < 1:
        digits = 4
    if code == "MYR":
        return f"RM {amount:,.{digits}f}"
    return f"{amount:,.{digits}f} {name(code)}"


def fetch_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "Friday"})
    with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


class Rates:
    def __init__(self, fetch: Callable[[str], dict] = fetch_json, clock: Callable[[], float] = time.time) -> None:
        self.fetch = fetch
        self.clock = clock
        self.table: dict[str, float] = {}
        self.updated = ""
        self.loaded_at = 0.0

    def load(self) -> dict[str, float]:
        if self.table and self.clock() - self.loaded_at < CACHE_SECONDS:
            return self.table
        error = None
        for url in SOURCES:
            try:
                data = self.fetch(url)
                rates = data.get("rates") or {}
                if not rates:
                    continue
                rates = {k.upper(): float(v) for k, v in rates.items()}
                rates["USD"] = 1.0
                self.table = rates
                stamp = data.get("time_last_update_unix")
                self.updated = (datetime.datetime.fromtimestamp(stamp).strftime("%m/%d") if stamp
                                else str(data.get("date", ""))[5:].replace("-", "/"))
                self.loaded_at = self.clock()
                return self.table
            except Exception as e:  # noqa: BLE001
                error = e
        if self.table:
            return self.table  # 拿不到新的:先用旧的
        raise ValueError("现在拿不到汇率(网络有问题),等一下再试") from error

    def rate(self, base: str, quote: str) -> float:
        table = self.load()
        if base not in table or quote not in table:
            raise ValueError("这个货币查不到汇率")
        return table[quote] / table[base]

    def convert(self, amount: float, base: str, quote: str) -> str:
        rate = self.rate(base, quote)
        result = amount * rate
        one = (f"1 {name(base)} = {money(rate, quote, rate=True)}" if rate >= 0.001
               else f"1 {name(quote)} = {money(1 / rate, base, rate=True)}")
        when = f",汇率日期 {self.updated}" if self.updated else ""
        return f"{money(amount, base)} ≈ {money(result, quote)}({one}{when};银行、换钱店的价格会有一点差别)。"

    def describe_rate(self, base: str, quote: str = "MYR") -> str:
        rate = self.rate(base, quote)
        when = f"(汇率日期 {self.updated})" if self.updated else ""
        if rate < 0.001:
            return f"1 {name(quote)} = {money(1 / rate, base, rate=True)}{when}。"
        return f"1 {name(base)} = {money(rate, quote, rate=True)}{when}。"


class FxAlerts:
    def __init__(self, path: str | os.PathLike, rates: Rates) -> None:
        self.path = Path(path)
        self.rates = rates
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        self.items: list[dict] = [a for a in (data.get("alerts") or []) if isinstance(a, dict)] if isinstance(data, dict) else []

    def _save(self) -> None:
        temp = self.path.with_name(self.path.name + ".tmp")
        temp.write_text(json.dumps({"alerts": self.items}, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(temp, self.path)

    def add(self, base: str, target: float, quote: str = "MYR", direction: str = "") -> str:
        base, quote = base.upper(), quote.upper()
        if base not in CURRENCIES or quote not in CURRENCIES or base == quote:
            raise ValueError("要说清楚是哪个货币")
        try:
            target = float(target)
        except (TypeError, ValueError):
            raise ValueError("目标汇率要是数字") from None
        if target <= 0:
            raise ValueError("目标汇率要大于 0")
        now = self.rates.rate(base, quote)
        if direction not in ("above", "below"):
            direction = "above" if now < target else "below"
        self.items = [a for a in self.items if not (a["base"] == base and a["quote"] == quote)]
        self.items.append({"base": base, "quote": quote, "target": target, "direction": direction})
        self._save()
        word = "涨到" if direction == "above" else "跌到"
        return (f"好,1 {name(base)}{word} {money(target, quote, rate=True)} {'以上' if direction == 'above' else '以下'}"
                f"就告诉你,也会推到手机。现在是 {money(now, quote, rate=True)}。")

    def remove(self, base: str) -> str:
        base = base.upper()
        before = len(self.items)
        self.items = [a for a in self.items if a["base"] != base]
        if len(self.items) == before:
            raise ValueError(f"没有{name(base)}的汇率提醒")
        self._save()
        return f"取消了{name(base)}的汇率提醒"

    def describe(self) -> str:
        if not self.items:
            return "没有汇率提醒。说“新币到 3.5 告诉我”就能设。"
        parts = [f"{name(a['base'])}{'涨到' if a['direction'] == 'above' else '跌到'} {money(a['target'], a['quote'], rate=True)}"
                 for a in self.items]
        return "汇率提醒:" + "、".join(parts) + "。"

    def check(self, who: str) -> list[str]:
        if not self.items:
            return []
        said, keep = [], []
        for alert in self.items:
            try:
                now = self.rates.rate(alert["base"], alert["quote"])
            except ValueError:
                keep.append(alert)
                continue
            hit = now >= alert["target"] if alert["direction"] == "above" else now <= alert["target"]
            if hit:
                said.append(f"{who},{name(alert['base'])}汇率到了:1 {name(alert['base'])} = {money(now, alert['quote'], rate=True)}"
                            f"(你设的是{'涨到' if alert['direction'] == 'above' else '跌到'} "
                            f"{money(alert['target'], alert['quote'], rate=True)})。"
                            "这个提醒做完就取消了。")
            else:
                keep.append(alert)
        if said:
            self.items = keep
            self._save()
        return said


# ======================== 一句话认出来 ========================

NUMBER = re.compile(r"(\d+(?:\.\d+)?)\s*(万|千|k)?", re.I)


def parse_convert(sentence: str) -> tuple[float, str, str] | None:
    """“100 美金是多少马币”“1000 马币换人民币多少” -> (100, "USD", "MYR")。"""
    if not re.search(r"多少|几多|换算|等于|折合", sentence) or re.search(r"汇率.*(到|提醒|告诉)", sentence):
        return None
    found = NUMBER.search(sentence)
    codes = mentions(sentence)
    if not found or not codes or len(codes) > 2:
        return None
    amount = float(found.group(1)) * {"万": 10000, "千": 1000, "k": 1000, "K": 1000}.get(found.group(2) or "", 1)
    if len(codes) == 1:
        if codes[0] == "MYR":
            return None  # 只说马币,不知道要换成什么
        return amount, codes[0], "MYR"
    return amount, codes[0], codes[1]


ALERT_RE = re.compile(r"(到|超过|高过|高于|低过|低于|跌到|涨到|跌破|突破|破)\s*(rm|RM)?\s*(\d+(?:\.\d+)?)\s*(的时候|时|就)?"
                      r"(告诉我|提醒我|通知我|叫我|跟我说)")


def parse_alert(sentence: str) -> tuple[str, float, str, str] | None:
    """“新币到 3.5 告诉我”“美金跌破 4.3 提醒我” -> (base, 目标, quote, 方向)。"""
    found = ALERT_RE.search(sentence)
    if not found:
        return None
    codes = [c for c in mentions(sentence[:found.start()]) if c != "MYR"] or mentions(sentence[:found.start()])
    if not codes:
        return None
    word = found.group(1)
    direction = "above" if word in ("超过", "高过", "高于", "涨到", "突破") else "below" if word in ("低过", "低于", "跌到", "跌破") else ""
    return codes[0], float(found.group(3)), "MYR", direction
