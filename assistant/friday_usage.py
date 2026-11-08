"""统计星期五每个月大概花了多少钱(按 Claude Opus 5.5 的标价估算,不是账单)。

准确的花费以 Claude Console 的 Usage 页面为准;这里只是让你在星期五的窗口里随时看到个大概。
"""

from __future__ import annotations

import datetime
import json
import os
import threading
from pathlib import Path
from typing import Callable

# 美元 / 每 100 万 token(Claude Opus 5.5 标价)
PRICES = {"input": 4.00, "output": 20.00, "write_5m": 5.00, "write_1h": 8.00, "read": 0.20}
SEARCH_PRICE = 0.01  # 每次联网搜索
DEFAULT_USD_TO_MYR = 4.1


def _get(obj, name: str, default=0):
    value = obj.get(name, default) if isinstance(obj, dict) else getattr(obj, name, default)
    return default if value is None else value


def request_cost(usage) -> float:
    """一次请求的花费(美元)。"""
    creation = _get(usage, "cache_creation", None)
    if creation is not None:
        write_5m = _get(creation, "ephemeral_5m_input_tokens")
        write_1h = _get(creation, "ephemeral_1h_input_tokens")
    else:
        write_5m, write_1h = _get(usage, "cache_creation_input_tokens"), 0
    server = _get(usage, "server_tool_use", None)
    searches = _get(server, "web_search_requests") if server is not None else 0
    tokens = (
        _get(usage, "input_tokens") * PRICES["input"]
        + _get(usage, "output_tokens") * PRICES["output"]
        + write_5m * PRICES["write_5m"]
        + write_1h * PRICES["write_1h"]
        + _get(usage, "cache_read_input_tokens") * PRICES["read"]
    )
    return tokens / 1_000_000 + searches * SEARCH_PRICE


class UsageMeter:
    """按月累计花费,存在 friday_usage.json 里(换月自动从零开始)。"""

    def __init__(self, path: str | Path, clock: Callable[[], datetime.datetime] = datetime.datetime.now,
                 usd_to_myr: float | None = None) -> None:
        self.path = Path(path)
        self.clock = clock
        self.rate = usd_to_myr or float(os.environ.get("FRIDAY_USD_TO_MYR") or DEFAULT_USD_TO_MYR)
        self.lock = threading.Lock()
        self.data = self._load()

    def _month(self) -> str:
        return self.clock().strftime("%Y-%m")

    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("usd"), (int, float)):
                return data
        except (OSError, ValueError):
            pass
        return {"month": self._month(), "usd": 0.0, "requests": 0}

    def add(self, usage) -> float:
        cost = request_cost(usage)
        with self.lock:
            if self.data.get("month") != self._month():
                self.data = {"month": self._month(), "usd": 0.0, "requests": 0}
            self.data["usd"] += cost
            self.data["requests"] += 1
            temp = self.path.with_name(self.path.name + ".tmp")
            try:
                temp.write_text(json.dumps(self.data), encoding="utf-8")
                os.replace(temp, self.path)
            except OSError:
                pass
        return cost

    def month_myr(self) -> float:
        with self.lock:
            usd = self.data["usd"] if self.data.get("month") == self._month() else 0.0
        return usd * self.rate

    def describe(self, usage, cost: float) -> str:
        """写进运行记录的一行,方便看缓存有没有生效。"""
        return (f"用量:新输入 {_get(usage, 'input_tokens')},缓存读 {_get(usage, 'cache_read_input_tokens')},"
                f"缓存写 {_get(usage, 'cache_creation_input_tokens')},输出 {_get(usage, 'output_tokens')}"
                f" → 约 RM {cost * self.rate:.3f}(本月约 RM {self.month_myr():.2f})")
