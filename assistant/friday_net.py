"""网速测试和断网记录(不花钱)。

网速:用 Cloudflare 的测速服务测延迟、下载、上传(大约 20 秒,会用掉 30MB 左右流量)。
断网:每 20 秒看一次能不能连上网,断了马上在电脑上说,恢复了再告诉你断了多久;记 30 天,可以问“今天断了几次网”。
"""

from __future__ import annotations

import datetime
import http.client
import json
import os
import socket
import statistics
import threading
import time
from pathlib import Path
from typing import Callable

HOST = "speed.cloudflare.com"
DOWN_BYTES = 25_000_000
UP_BYTES = 8_000_000
PART_SECONDS = 10.0  # 下载、上传各最多测 10 秒
PROBES = (("1.1.1.1", 443), ("8.8.8.8", 53), ("208.67.222.222", 443))
CHECK_SECONDS = 20
MIN_OUTAGE = 60  # 断了一分钟以上才记、才说(一闪而过的不算)
KEEP_DAYS = 30


def online(timeout: float = 3.0) -> bool:
    for host, port in PROBES:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            continue
    return False


# ======================== 测网速 ========================


def measure(connect: Callable[[], http.client.HTTPConnection] | None = None,
            clock: Callable[[], float] = time.perf_counter) -> dict:
    """-> {"ping": 毫秒, "jitter": 毫秒, "down": Mbps, "up": Mbps}。连不上抛 OSError。"""
    connect = connect or (lambda: http.client.HTTPSConnection(HOST, timeout=15))
    conn = connect()
    try:
        times = []
        for _ in range(6):
            started = clock()
            conn.request("GET", "/__down?bytes=0", headers={"User-Agent": "Friday"})
            response = conn.getresponse()
            response.read()
            times.append((clock() - started) * 1000)
        times = sorted(times[1:])  # 第一次包括建立连接,不算
        ping = statistics.median(times)
        jitter = statistics.mean(abs(a - b) for a, b in zip(times, times[1:])) if len(times) > 1 else 0.0

        started = clock()
        conn.request("GET", f"/__down?bytes={DOWN_BYTES}", headers={"User-Agent": "Friday"})
        response = conn.getresponse()
        got = 0
        while True:
            chunk = response.read(256 * 1024)
            if not chunk:
                break
            got += len(chunk)
            if clock() - started > PART_SECONDS:
                break
        down_seconds = max(clock() - started, 1e-6)
        down = got * 8 / down_seconds / 1e6
        conn.close()  # 没读完就断开,重新连一个测上传

        conn = connect()
        body = b"0" * UP_BYTES
        started = clock()
        conn.request("POST", "/__up", body=body, headers={"User-Agent": "Friday", "Content-Type": "text/plain"})
        conn.getresponse().read()
        up = len(body) * 8 / max(clock() - started, 1e-6) / 1e6
    finally:
        conn.close()
    return {"ping": round(ping), "jitter": round(jitter), "down": round(down, 1), "up": round(up, 1)}


def ping_word(ms: float) -> str:
    if ms < 30:
        return "很低,打游戏很顺"
    if ms < 60:
        return "正常,打游戏没问题"
    if ms < 100:
        return "有点高,打对战游戏可能会觉得慢半拍"
    return "很高,打游戏会卡"


def describe_speed(result: dict) -> str:
    text = (f"网速测好了:下载 {result['down']} Mbps,上传 {result['up']} Mbps,"
            f"延迟 {result['ping']} 毫秒({ping_word(result['ping'])})")
    if result.get("jitter", 0) >= 20:
        text += f",抖动 {result['jitter']} 毫秒,网络不太稳"
    if result["down"] < 10:
        text += "。下载很慢,可以重开路由器试试,或者看看是不是有别的东西在下载"
    return text + "。(Cloudflare 测速,结果会受 Wi-Fi 信号影响)"


class SpeedTest:
    def __init__(self, run: Callable[[], dict] = measure, clock: Callable[[], float] = time.time) -> None:
        self.run = run
        self.clock = clock
        self.running = False
        self.last: dict | None = None
        self._lock = threading.Lock()

    def start(self, on_done: Callable[[str], None]) -> str:
        with self._lock:
            if self.running:
                return "正在测网速,等一下就好。"
            self.running = True

        def work() -> None:
            try:
                result = self.run()
                self.last = {**result, "time": self.clock()}
                text = describe_speed(result)
            except Exception:  # noqa: BLE001
                text = "测不了网速:连不上测速服务器。看看网络有没有断,或者等一下再试。"
            finally:
                with self._lock:
                    self.running = False
            on_done(text)

        threading.Thread(target=work, daemon=True, name="friday-speedtest").start()
        return "开始测网速,大约 20 秒,测的时候网络会忙一下。"


# ======================== 断网记录 ========================


def _hm(stamp: float) -> str:
    return datetime.datetime.fromtimestamp(stamp).strftime("%H:%M")


def _length(seconds: float) -> str:
    minutes = round(seconds / 60)
    if minutes < 1:
        return f"{int(seconds)} 秒"
    if minutes < 60:
        return f"{minutes} 分钟"
    return f"{minutes // 60} 小时" + (f" {minutes % 60} 分钟" if minutes % 60 else "")


class NetWatch:
    def __init__(self, path: str | os.PathLike, probe: Callable[[], bool] = online,
                 clock: Callable[[], float] = time.time, interval: float = CHECK_SECONDS) -> None:
        self.path = Path(path)
        self.probe = probe
        self.clock = clock
        self.interval = interval
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        self.outages: list[dict] = [o for o in (data.get("outages") or []) if isinstance(o, dict)] if isinstance(data, dict) else []
        self.fails = 0
        self.first_fail: float | None = None
        self.down_since: float | None = None
        self.last_tick: float | None = None

    def _save(self) -> None:
        cutoff = self.clock() - KEEP_DAYS * 86400
        self.outages = [o for o in self.outages if o["end"] >= cutoff]
        try:
            self.path.write_text(json.dumps({"outages": self.outages}, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass

    def tick(self) -> tuple[str, str] | None:
        """看一次。-> ("down", 话) 刚断网;("up", 话) 恢复了;其他时候 None。"""
        now = self.clock()
        if self.last_tick is not None and now - self.last_tick > self.interval * 4:
            self.fails, self.first_fail = 0, None  # 电脑刚睡醒:网络要一会儿才连上,不算断网
            if self.down_since is not None:
                self.down_since = None
        self.last_tick = now
        if not self.probe():
            self.fails += 1
            if self.first_fail is None:
                self.first_fail = now
            if self.fails == 2 and self.down_since is None:
                self.down_since = self.first_fail
                return "down", "网络好像断了,我在盯着,恢复了告诉你。"
            return None
        self.fails, self.first_fail = 0, None
        if self.down_since is None:
            return None
        start, self.down_since = self.down_since, None
        length = now - start
        if length < MIN_OUTAGE:
            return None
        self.outages.append({"start": start, "end": now})
        self._save()
        return "up", f"网络恢复了,刚才断了 {_length(length)}({_hm(start)} 到 {_hm(now)})。"

    def report(self, period: str = "today") -> str:
        now = datetime.datetime.fromtimestamp(self.clock())
        if period == "week":
            first = (now - datetime.timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
            label = "这周"
        else:
            first = now.replace(hour=0, minute=0, second=0, microsecond=0)
            label = "今天"
        found = [o for o in self.outages if o["end"] >= first.timestamp()]
        current = ""
        if self.down_since is not None:
            current = f"现在网络是断的(从 {_hm(self.down_since)} 开始)。"
        if not found:
            return current + f"{label}网络没有断过(只算星期五开着的时候)。"
        total = sum(o["end"] - o["start"] for o in found)
        parts = [f"{datetime.datetime.fromtimestamp(o['start']):%m/%d %H:%M} 断了 {_length(o['end'] - o['start'])}"
                 if period == "week" else f"{_hm(o['start'])}~{_hm(o['end'])}" for o in found[-8:]]
        return current + f"{label}断了 {len(found)} 次网,一共 {_length(total)}:" + "、".join(parts) + "。"

    def start(self, on_event: Callable[[str, str], None]) -> None:
        def loop() -> None:
            while True:
                time.sleep(self.interval)
                try:
                    event = self.tick()
                    if event:
                        on_event(*event)
                except Exception:
                    pass

        threading.Thread(target=loop, daemon=True, name="friday-netwatch").start()
