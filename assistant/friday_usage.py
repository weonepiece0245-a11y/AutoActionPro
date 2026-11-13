"""统计星期五每个月大概花了多少钱(按各模型的标价估算,不是账单)。

准确的花费以 Claude Console 的 Usage 页面为准;这里只是让你在星期五的窗口里随时看到个大概。
"""

from __future__ import annotations

import datetime
import json
import os
import threading
from pathlib import Path
from typing import Callable

# 美元 / 每 100 万 token(标价)
MODEL_PRICES = {
    "claude-opus-5-5": {"input": 4.00, "output": 20.00, "write_5m": 5.00, "write_1h": 8.00, "read": 0.20},
    "claude-sonnet-5-5": {"input": 2.00, "output": 10.00, "write_5m": 2.50, "write_1h": 4.00, "read": 0.20},
    "claude-haiku-5-5": {"input": 0.10, "output": 0.50, "write_5m": 0.125, "write_1h": 0.20, "read": 0.01},
}
PRICES = MODEL_PRICES["claude-opus-5-5"]  # 不认识的模型按 Opus 算(宁可估高)
SEARCH_PRICE = 0.01  # 每次联网搜索
DEFAULT_USD_TO_MYR = 4.1


def _get(obj, name: str, default=0):
    value = obj.get(name, default) if isinstance(obj, dict) else getattr(obj, name, default)
    return default if value is None else value


def prices_for(model: str | None) -> dict:
    model = model or ""
    for name, prices in MODEL_PRICES.items():
        if model.startswith(name):
            return prices
    return PRICES


def request_cost(usage, model: str | None = None) -> float:
    """一次请求的花费(美元)。"""
    price = prices_for(model)
    creation = _get(usage, "cache_creation", None)
    if creation is not None:
        write_5m = _get(creation, "ephemeral_5m_input_tokens")
        write_1h = _get(creation, "ephemeral_1h_input_tokens")
    else:
        write_5m, write_1h = _get(usage, "cache_creation_input_tokens"), 0
    server = _get(usage, "server_tool_use", None)
    searches = _get(server, "web_search_requests") if server is not None else 0
    tokens = (
        _get(usage, "input_tokens") * price["input"]
        + _get(usage, "output_tokens") * price["output"]
        + write_5m * price["write_5m"]
        + write_1h * price["write_1h"]
        + _get(usage, "cache_read_input_tokens") * price["read"]
    )
    return tokens / 1_000_000 + searches * SEARCH_PRICE


def model_label(model: str | None) -> str:
    name = (model or "").lower()
    for label in ("Opus", "Sonnet", "Haiku"):
        if label.lower() in name:
            return label
    return "其他"


class UsageMeter:
    """按天累计花费,存在 friday_usage.json 里;可以算本月、或者从某天以来花了多少。"""

    KEEP_DAYS = 400

    def __init__(self, path: str | Path, clock: Callable[[], datetime.datetime] = datetime.datetime.now,
                 usd_to_myr: float | None = None) -> None:
        self.path = Path(path)
        self.clock = clock
        self.rate = usd_to_myr or float(os.environ.get("FRIDAY_USD_TO_MYR") or DEFAULT_USD_TO_MYR)
        self.lock = threading.Lock()
        self.data = self._load()

    def _month(self) -> str:
        return self.clock().strftime("%Y-%m")

    def _today(self) -> str:
        return self.clock().strftime("%Y-%m-%d")

    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = None
        if isinstance(data, dict) and isinstance(data.get("days"), dict):
            def counts(key):
                value = data.get(key)
                return {k: v for k, v in value.items() if isinstance(v, (int, float, dict))} if isinstance(value, dict) else {}

            return {"days": {k: float(v) for k, v in data["days"].items() if isinstance(v, (int, float))},
                    "requests": int(data.get("requests", 0)),
                    "calls": counts("calls"), "models": counts("models"), "local": counts("local")}
        if isinstance(data, dict) and isinstance(data.get("usd"), (int, float)):  # 旧版只存了本月总数
            month = data.get("month") or self._month()
            return {"days": {f"{month}-01": float(data["usd"])}, "requests": int(data.get("requests", 0)),
                    "calls": {}, "models": {}, "local": {}}
        return {"days": {}, "requests": 0, "calls": {}, "models": {}, "local": {}}

    def _save(self) -> None:
        for key in ("days", "calls", "models", "local"):
            for old in sorted(self.data[key])[:-self.KEEP_DAYS]:
                del self.data[key][old]
        temp = self.path.with_name(self.path.name + ".tmp")
        try:
            temp.write_text(json.dumps(self.data), encoding="utf-8")
            os.replace(temp, self.path)
        except OSError:
            pass

    def add(self, usage, model: str | None = None) -> float:
        cost = request_cost(usage, model)
        with self.lock:
            days = self.data["days"]
            today = self._today()
            days[today] = days.get(today, 0.0) + cost
            self.data["requests"] += 1
            self.data["calls"][today] = self.data["calls"].get(today, 0) + 1
            label = model_label(model)
            per_model = self.data["models"].setdefault(today, {})
            per_model[label] = per_model.get(label, 0.0) + cost
            self._save()
        return cost

    def add_local(self) -> None:
        """记一次在电脑上直接办好、没问 Claude 的简单指令。"""
        with self.lock:
            today = self._today()
            self.data["local"][today] = self.data["local"].get(today, 0) + 1
            self._save()

    def between(self, first: str, last: str) -> dict:
        """first 到 last(YYYY-MM-DD,都含)的花费:{usd, calls, local, models: {名字: usd}, days: {日期: usd}}。"""
        with self.lock:
            inside = lambda d: first <= d <= last
            models: dict[str, float] = {}
            for day, per in self.data["models"].items():
                if inside(day) and isinstance(per, dict):
                    for name, usd in per.items():
                        models[name] = models.get(name, 0.0) + usd
            days = {d: v for d, v in self.data["days"].items() if inside(d)}
            return {"usd": sum(days.values()), "days": days, "models": models,
                    "calls": sum(v for d, v in self.data["calls"].items() if inside(d)),
                    "local": sum(v for d, v in self.data["local"].items() if inside(d))}

    def month_usd(self) -> float:
        month = self._month()
        with self.lock:
            return sum(v for day, v in self.data["days"].items() if day.startswith(month))

    def since_usd(self, day: str) -> float:
        """从某天(YYYY-MM-DD,含当天)到现在花了多少美元。"""
        with self.lock:
            return sum(v for d, v in self.data["days"].items() if d >= day)

    def month_myr(self) -> float:
        return self.month_usd() * self.rate

    def describe(self, usage, cost: float, model: str | None = None) -> str:
        """写进运行记录的一行,方便看缓存有没有生效。"""
        name = f"[{model}] " if model else ""
        return (f"{name}用量:新输入 {_get(usage, 'input_tokens')},缓存读 {_get(usage, 'cache_read_input_tokens')},"
                f"缓存写 {_get(usage, 'cache_creation_input_tokens')},输出 {_get(usage, 'output_tokens')}"
                f" → 约 RM {cost * self.rate:.3f}(本月约 RM {self.month_myr():.2f})")
