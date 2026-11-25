"""语音记账:说一句“午饭 15 块”就记下来(马币),月底给你汇总。

账本存在电脑上(friday_ledger.json),是真正的记录;
登录了 Google 的话,同时整本抄一份到 Google 表格“星期五记账”,手机上也能看。
"""

from __future__ import annotations

import calendar
import datetime
import json
import os
from pathlib import Path
from typing import Callable

CATEGORIES = ["餐饮", "交通", "购物", "娱乐", "账单", "医疗", "其他"]
SHEET_TITLE = "星期五记账"
HEADER = ["编号", "日期", "项目", "分类", "金额 (RM)", "备注"]
SUMMARY_HOUR = 21  # 每月最后一天晚上 9 点后做月底汇总
MAX_AMOUNT = 1_000_000

Clock = Callable[[], datetime.datetime]


def money(value: float) -> str:
    return f"RM {value:,.2f}"


class Ledger:
    def __init__(self, path: str | os.PathLike, clock: Clock = datetime.datetime.now) -> None:
        self.path = Path(path)
        self.clock = clock
        self.entries: list[dict] = []
        self.sheet_id = ""
        self.summarized: list[str] = []  # 已经汇总过的月份
        self.sync: Callable[[list[list]], None] | None = None  # 抄到 Google 表格(由 Toolbox 设置)
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if isinstance(data, dict):
            self.entries = [e for e in data.get("entries", []) if isinstance(e, dict)]
            self.sheet_id = str(data.get("sheet_id", ""))
            self.summarized = list(data.get("summarized", []))

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_name(self.path.name + ".tmp")
        temp.write_text(json.dumps({"entries": self.entries, "sheet_id": self.sheet_id,
                                    "summarized": self.summarized}, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(temp, self.path)

    def rows(self) -> list[list]:
        """给 Google 表格的整本账(第一行是标题)。"""
        return [HEADER] + [[e["id"], e["date"], e["item"], e["category"], e["amount"], e.get("note", "")]
                           for e in sorted(self.entries, key=lambda e: (e["date"], e["id"]))]

    def _synced(self) -> str:
        if self.sync is None:
            return ""
        try:
            self.sync(self.rows())
            return ",已同步到 Google 表格“星期五记账”"
        except Exception as e:
            return f"(同步到 Google 表格失败:{e};电脑上已经记好了)"

    # ---------- 记 / 删 ----------
    def add(self, item: str, amount, category: str = "其他", date: str = "", note: str = "") -> str:
        item = str(item or "").strip()
        if not item:
            raise ValueError("要写花在什么上(item)")
        try:
            amount = round(float(amount), 2)
        except (TypeError, ValueError):
            raise ValueError("金额要是数字") from None
        if not 0 < amount < MAX_AMOUNT:
            raise ValueError("金额要大于 0")
        today = self.clock().date()
        if date:
            try:
                day = datetime.date.fromisoformat(str(date))
            except ValueError:
                raise ValueError("日期格式要是 YYYY-MM-DD") from None
            if day > today:
                raise ValueError("不能记未来的账")
        else:
            day = today
        category = category if category in CATEGORIES else "其他"
        number = max((e["id"] for e in self.entries), default=0) + 1
        entry = {"id": number, "date": day.isoformat(), "item": item[:60], "amount": amount, "category": category}
        if note:
            entry["note"] = str(note)[:100]
        self.entries.append(entry)
        self._save()
        month = day.strftime("%Y-%m")
        total = self.total(month)
        return (f"已记账 #{number}:{entry['date']} {entry['item']} {money(amount)}({category})。"
                f"{int(month[5:])} 月到现在共花 {money(total)}{self._synced()}")

    def delete(self, number) -> str:
        if str(number).lower() in ("last", "最后", "上一笔", ""):
            if not self.entries:
                raise ValueError("账本是空的")
            entry = max(self.entries, key=lambda e: e["id"])
        else:
            try:
                number = int(str(number).lstrip("#"))
            except ValueError:
                raise ValueError("要删哪一笔?给编号,或者 last 表示最后一笔") from None
            entry = next((e for e in self.entries if e["id"] == number), None)
            if entry is None:
                raise ValueError(f"没有编号 #{number} 这一笔")
        self.entries.remove(entry)
        self._save()
        return f"已删掉 #{entry['id']}:{entry['date']} {entry['item']} {money(entry['amount'])}{self._synced()}"

    # ---------- 看 ----------
    def month_entries(self, month: str) -> list[dict]:
        return sorted((e for e in self.entries if e["date"].startswith(month)), key=lambda e: (e["date"], e["id"]))

    def total(self, month: str) -> float:
        return round(sum(e["amount"] for e in self.month_entries(month)), 2)

    def list(self, month: str = "") -> str:
        month = month or self.clock().strftime("%Y-%m")
        entries = self.month_entries(month)
        if not entries:
            return f"{month} 还没有记账"
        lines = [f"#{e['id']} {e['date'][5:]} {e['item']} {money(e['amount'])}({e['category']})"
                 + (f" {e['note']}" if e.get("note") else "") for e in entries[-60:]]
        more = f"(只列出最后 60 笔,共 {len(entries)} 笔)\n" if len(entries) > 60 else ""
        return f"{month} 的账,共 {money(self.total(month))}:\n{more}" + "\n".join(lines)

    def summary(self, month: str = "") -> str:
        month = month or self.clock().strftime("%Y-%m")
        entries = self.month_entries(month)
        year, mon = int(month[:4]), int(month[5:7])
        if not entries:
            return f"{year} 年 {mon} 月没有记账"
        total = self.total(month)
        by_category: dict[str, float] = {}
        for e in entries:
            by_category[e["category"]] = by_category.get(e["category"], 0) + e["amount"]
        parts = "、".join(f"{c} {money(v)}({v / total:.0%})"
                         for c, v in sorted(by_category.items(), key=lambda kv: -kv[1]))
        biggest = max(entries, key=lambda e: e["amount"])
        days = len({e["date"] for e in entries})
        prev = (datetime.date(year, mon, 1) - datetime.timedelta(days=1)).strftime("%Y-%m")
        prev_total = self.total(prev)
        compare = ""
        if prev_total:
            diff = total - prev_total
            compare = f";比上个月{'多' if diff > 0 else '少'}花 {money(abs(diff))}" if abs(diff) >= 0.01 else ";和上个月差不多"
        return (f"{year} 年 {mon} 月共花 {money(total)},{len(entries)} 笔,记了 {days} 天。"
                f"分类:{parts}。最大的一笔是 {biggest['date'][5:]} {biggest['item']} {money(biggest['amount'])}{compare}。")

    # ---------- 月底汇总 ----------
    def due_summary(self) -> str | None:
        """该做月底汇总的月份:当月最后一天晚上 9 点后做这个月;错过了(电脑没开)就在下个月补上个月。"""
        now = self.clock()
        this_month = now.strftime("%Y-%m")
        last_day = calendar.monthrange(now.year, now.month)[1]
        if now.day == last_day and now.hour >= SUMMARY_HOUR and this_month not in self.summarized:
            return this_month if self.month_entries(this_month) else None
        prev = (now.date().replace(day=1) - datetime.timedelta(days=1)).strftime("%Y-%m")
        if prev not in self.summarized and self.month_entries(prev):
            return prev
        return None

    def mark_summarized(self, month: str) -> None:
        if month not in self.summarized:
            self.summarized = (self.summarized + [month])[-24:]
            self._save()


def sheet_sync(sheets_api_factory: Callable[[], object], ledger: Ledger) -> Callable[[list[list]], None]:
    """把整本账写进 Google 表格“星期五记账”(没有就新建,表格 id 记在账本里)。"""

    def sync(rows: list[list]) -> None:
        try:
            write(rows)
        except Exception as e:
            if not ledger.sheet_id or "404" not in str(e):
                raise
            ledger.sheet_id = ""  # 表格被删掉了:新建一个
            write(rows)

    def write(rows: list[list]) -> None:
        api = sheets_api_factory()
        if not ledger.sheet_id:
            sheet = api.create(body={"properties": {"title": SHEET_TITLE}}, fields="spreadsheetId").execute()
            ledger.sheet_id = sheet["spreadsheetId"]
            ledger._save()
        values = api.values()
        values.clear(spreadsheetId=ledger.sheet_id, range="A:F").execute()
        values.update(spreadsheetId=ledger.sheet_id, range="A1", valueInputOption="USER_ENTERED",
                      body={"values": rows}).execute()

    return sync
