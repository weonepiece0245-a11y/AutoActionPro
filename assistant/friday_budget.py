"""每月预算、余额,以及(可选)从 Claude Console 读取真实花费。

- 每月预算(马币):用到 80% 提醒;90% 起自动只用 Sonnet;用完就暂停,不再问 Claude。
- 余额:Claude 没有读取余额的接口,所以由你在设定里填“哪天充了多少美元”,
  星期五用那天以来的花费算出还剩多少。
- Console 真实花费:需要 Admin API Key(sk-ant-admin 开头,要在 Console 的组织里创建)。
  只用它读花费报表,不发消息、不花钱。没有的话就用星期五自己的估算。
"""

from __future__ import annotations

import datetime
import json
import os
import threading
import time
import urllib.parse
import urllib.request
from typing import Callable

COST_REPORT_URL = "https://api.anthropic.com/v1/organizations/cost_report"
CONSOLE_REFRESH = 30 * 60  # 每半小时读一次 Console
WARN_RATIO = 0.8
SAVER_RATIO = 0.9


class BudgetExceeded(Exception):
    """本月预算用完了。"""


def _env_float(name: str) -> float:
    try:
        return max(0.0, float(os.environ.get(name, "") or 0))
    except ValueError:
        return 0.0


def fetch_json(url: str, key: str, timeout: float = 30) -> dict:
    request = urllib.request.Request(url, headers={
        "x-api-key": key, "anthropic-version": "2023-06-01", "User-Agent": "friday-assistant"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


class ConsoleCost:
    """用 Admin API Key 读 Console 的花费报表(美元)。"""

    def __init__(self, key: str, fetch: Callable[[str, str], dict] = fetch_json) -> None:
        self.key = key
        self.fetch = fetch

    def cost_since(self, day: datetime.date, now: datetime.datetime | None = None) -> float:
        now = now or datetime.datetime.now(datetime.timezone.utc)
        params = {
            "starting_at": f"{day.isoformat()}T00:00:00Z",
            "ending_at": (now + datetime.timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z"),
            "bucket_width": "1d",
            "limit": "31",
        }
        total_cents = 0.0
        page = None
        for _ in range(20):  # 最多翻 20 页(一年多)
            query = dict(params, **({"page": page} if page else {}))
            data = self.fetch(f"{COST_REPORT_URL}?{urllib.parse.urlencode(query)}", self.key)
            for bucket in data.get("data", []):
                for result in bucket.get("results", []):
                    try:
                        total_cents += float(result.get("amount", 0))  # 单位是美分
                    except (TypeError, ValueError):
                        pass
            if not data.get("has_more") or not data.get("next_page"):
                break
            page = data["next_page"]
        return total_cents / 100


class Budget:
    """预算、余额和 Console 花费;设置都读 .env(齿轮设定里改)。"""

    def __init__(self, meter, notify: Callable[[str], None] | None = None,
                 clock: Callable[[], datetime.datetime] = datetime.datetime.now,
                 console_factory: Callable[[str], ConsoleCost] = ConsoleCost) -> None:
        self.meter = meter
        self.notify = notify
        self.clock = clock
        self.console_factory = console_factory
        self.lock = threading.Lock()
        self.console: dict = {}  # {"month_usd", "since_usd", "at", "error"}
        self.notified: dict[str, str] = {}  # 提醒过的(哪个月提醒过哪一级)

    # ---------- 设置 ----------
    @property
    def budget_myr(self) -> float:
        return _env_float("FRIDAY_BUDGET_MYR")

    @property
    def credit_usd(self) -> float:
        return _env_float("FRIDAY_CREDIT_USD")

    @property
    def credit_since(self) -> str:
        return os.environ.get("FRIDAY_CREDIT_SINCE", "").strip()

    @property
    def admin_key(self) -> str:
        return os.environ.get("FRIDAY_ADMIN_KEY", "").strip()

    # ---------- Console ----------
    def refresh_console(self) -> dict:
        key = self.admin_key
        if not key:
            with self.lock:
                self.console = {}
            return {}
        console = self.console_factory(key)
        today = self.clock().date()
        result: dict = {"at": time.time()}
        try:
            result["month_usd"] = console.cost_since(today.replace(day=1))
            since = self.credit_since
            if since:
                result["since_usd"] = console.cost_since(datetime.date.fromisoformat(since))
        except Exception as e:
            result = {"at": time.time(), "error": self._describe_error(e)}
        with self.lock:
            self.console = result
        return result

    @staticmethod
    def _describe_error(error: Exception) -> str:
        code = getattr(error, "code", None)
        if code in (401, 403):
            return "Admin Key 无效或没有权限"
        if code == 404:
            return "这个账户不能用 Admin API(个人账户要先在 Console 设置组织)"
        return f"读不到 Console:{error}"

    def start(self, interval: float = CONSOLE_REFRESH) -> None:
        def run():
            while True:
                try:
                    self.refresh_console()
                    self.check()
                except Exception:
                    pass
                time.sleep(interval)

        threading.Thread(target=run, daemon=True, name="friday-budget").start()

    # ---------- 花费 ----------
    def month_usd(self) -> tuple[float, str]:
        """(本月花费美元, 来源 "console" 或 "estimate")"""
        with self.lock:
            console = dict(self.console)
        if "month_usd" in console:
            return console["month_usd"], "console"
        return self.meter.month_usd(), "estimate"

    def month_myr(self) -> tuple[float, str]:
        usd, source = self.month_usd()
        return usd * self.meter.rate, source

    def remaining_usd(self) -> float | None:
        """余额 = 充值金额 - 充值那天以来的花费。没填充值就返回 None。"""
        credit, since = self.credit_usd, self.credit_since
        if not credit or not since:
            return None
        with self.lock:
            console = dict(self.console)
        spent = console["since_usd"] if "since_usd" in console else self.meter.since_usd(since)
        return credit - spent

    def ratio(self) -> float:
        budget = self.budget_myr
        if not budget:
            return 0.0
        return self.month_myr()[0] / budget

    def saving(self) -> bool:
        """快到预算了:只用 Sonnet。"""
        return self.ratio() >= SAVER_RATIO

    def before_ask(self) -> None:
        """每次问 Claude 之前:预算用完了就不问。"""
        if self.budget_myr and self.ratio() >= 1:
            spent = self.month_myr()[0]
            raise BudgetExceeded(
                f"这个月的预算 RM {self.budget_myr:g} 已经用完了(约 RM {spent:.2f}),我先暂停,不再花钱。"
                "想继续用,打开右上角齿轮设定调高预算;下个月 1 号会自动恢复。")
        self.check()

    def check(self) -> None:
        """到 80%、90% 时各提醒一次(每个月)。"""
        if self.notify is None or not self.budget_myr:
            return
        ratio = self.ratio()
        month = self.clock().strftime("%Y-%m")
        for level, ratio_needed, text in (
            ("100", 1.0, "这个月的预算已经用完,星期五暂停回答了。可以在齿轮设定里调高预算。"),
            ("90", SAVER_RATIO, "这个月的预算已经用了 90%,接下来自动只用 Sonnet 5.5 来省钱。"),
            ("80", WARN_RATIO, "这个月的预算已经用了 80%,留意一下。"),
        ):
            if ratio >= ratio_needed:
                if self.notified.get(level) != month:
                    self.notified[level] = month
                    for lower in ("80", "90"):
                        if int(lower) < int(level):
                            self.notified[lower] = month  # 直接跳到高一级时,低的不用再提醒
                    self.notify(text)
                break

    # ---------- 给窗口看 ----------
    def state(self) -> dict:
        spent, source = self.month_myr()
        with self.lock:
            console = dict(self.console)
        remaining = self.remaining_usd()
        return {
            "budget_myr": self.budget_myr,
            "spent_myr": round(spent, 2),
            "source": source,
            "ratio": round(self.ratio(), 3),
            "credit_usd": self.credit_usd,
            "credit_since": self.credit_since,
            "remaining_usd": None if remaining is None else round(remaining, 2),
            "admin_key_set": bool(self.admin_key),
            "console_error": console.get("error", ""),
        }
