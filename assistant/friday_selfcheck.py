"""自我检查:半夜自动更新完,把常用的功能都试跑一遍(只读、不改东西、不花钱);
哪个坏了,早安简报里告诉你,手机上也会收到。也可以随时说“自我检查”。

试的东西:天气、Spotify 连线、开程序要用的开始菜单清单、提醒、Google、记忆、Claude 钥匙、Discord。
没设定的(例如没连 Spotify)不算坏,跳过。存在 friday_selfcheck.json。
"""

from __future__ import annotations

import concurrent.futures
import datetime
import json
import os
import threading
from pathlib import Path
from typing import Callable

TIMEOUT = 25
SKIP = "skip"  # 检查函数返回这个:没设定,不用检查


class SelfCheck:
    def __init__(self, path: str | os.PathLike, checks: dict[str, Callable[[], object]],
                 clock: Callable[[], datetime.datetime] = datetime.datetime.now, timeout: float = TIMEOUT) -> None:
        """checks:{名字: 检查函数};函数出错(抛异常)就算坏了,返回 SKIP 就跳过,返回别的字串当成注意事项。"""
        self.path = Path(path)
        self.checks = checks
        self.clock = clock
        self.timeout = timeout
        self.lock = threading.Lock()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        self.state: dict = data if isinstance(data, dict) else {}

    def _save(self) -> None:
        temp = self.path.with_name(f"{self.path.name}.{threading.get_ident()}.tmp")
        temp.write_text(json.dumps(self.state, ensure_ascii=False), encoding="utf-8")
        os.replace(temp, self.path)

    def run(self) -> list[dict]:
        """全部试一遍:[{name, status: ok/skip/bad, detail}]。"""
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(self.checks)))
        futures = {name: pool.submit(check) for name, check in self.checks.items()}
        results = []
        for name, future in futures.items():
            try:
                value = future.result(timeout=self.timeout)
            except concurrent.futures.TimeoutError:
                results.append({"name": name, "status": "bad", "detail": f"{self.timeout} 秒没反应"})
                continue
            except Exception as e:  # noqa: BLE001
                detail = str(e).strip() or type(e).__name__
                results.append({"name": name, "status": "bad", "detail": detail[:120]})
                continue
            if value == SKIP:
                results.append({"name": name, "status": "skip", "detail": "没设定"})
            else:
                results.append({"name": name, "status": "ok", "detail": str(value or "")[:120]})
        pool.shutdown(wait=False, cancel_futures=True)
        with self.lock:
            self.state = {"time": self.clock().strftime("%Y-%m-%d %H:%M"), "results": results,
                          "reported": not any(r["status"] == "bad" for r in results)}
            self._save()
        return results

    @staticmethod
    def describe(results: list[dict]) -> str:
        bad = [r for r in results if r["status"] == "bad"]
        good = [r["name"] for r in results if r["status"] == "ok"]
        skipped = [r["name"] for r in results if r["status"] == "skip"]
        if not bad:
            text = "自我检查:都正常(" + "、".join(good) + ")。"
        else:
            text = "自我检查:" + ";".join(f"{r['name']} 有问题({r['detail']})" for r in bad) + "。"
            if good:
                text += "其他正常:" + "、".join(good) + "。"
        if skipped:
            text += "没设定所以没检查:" + "、".join(skipped) + "。"
        return text

    def pending_report(self) -> str | None:
        """有坏掉的、还没告诉你:要说的话(说一次)。"""
        with self.lock:
            if self.state.get("reported", True):
                return None
            results = self.state.get("results") or []
            self.state["reported"] = True
            self._save()
        bad = [r for r in results if r["status"] == "bad"]
        if not bad:
            return None
        return (f"昨晚({self.state.get('time', '')})自我检查发现:" + ";".join(f"{r['name']} 有问题({r['detail']})" for r in bad)
                + "。可以说“自我检查”再试一次,或者截图给我看。")
