"""电脑温度:显卡(NVIDIA 驱动自带的 nvidia-smi)和 CPU(要开着 Libre Hardware Monitor),太热就提醒(不花钱)。

CPU 温度 Windows 自己不给,要靠 Libre Hardware Monitor:开着它就行(星期五用它的 WMI 读);
在它的 Options 里打开 Remote Web Server(端口 8085)会读得更快。只读温度,不改任何设定。
打游戏时太热会用右下角小通知提醒(30 分钟最多一次)。
"""

from __future__ import annotations

import json
import re
import subprocess
import time
import urllib.request
from typing import Callable

CPU_HOT = 92  # 很多 CPU 满载会跑到 90 度左右(AMD 新款设计上会到 95),再高才提醒
GPU_HOT = 85
ALERT_EVERY = 30 * 60
LHM_URL = "http://127.0.0.1:8085/data.json"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
WMI_QUERY = ("Get-CimInstance -Namespace root/LibreHardwareMonitor -ClassName Sensor -ErrorAction Stop | "
             "Where-Object { $_.SensorType -eq 'Temperature' } | Select-Object Name,Value,Parent,Identifier | "
             "ConvertTo-Json -Compress")
CPU_BEST = ("package", "tctl", "tdie", "core max", "core average", "cpu")


def _value(text) -> float | None:
    found = re.search(r"-?\d+(?:[.,]\d+)?", str(text or ""))
    if not found:
        return None
    try:
        value = float(found.group(0).replace(",", "."))
    except ValueError:
        return None
    return value if 0 < value < 130 else None


def _pick_cpu(sensors: list[tuple[str, str, float]]) -> float | None:
    """[(名字, 所属硬件, 温度)] -> CPU 温度:先找 Package / Tctl,没有就用 CPU 那几个里最高的。"""
    cpu = [(n.lower(), v) for n, parent, v in sensors if "cpu" in parent.lower() and v is not None]
    for word in CPU_BEST:
        hits = [v for n, v in cpu if word in n]
        if hits:
            return max(hits)
    return max((v for _, v in cpu), default=None)


def _pick_gpu(sensors: list[tuple[str, str, float]]) -> float | None:
    gpu = [(n.lower(), v) for n, parent, v in sensors if "gpu" in parent.lower() and v is not None]
    core = [v for n, v in gpu if "core" in n or n == "gpu"]
    return max(core or [v for _, v in gpu], default=None)


def lhm_http(fetch: Callable[[str], bytes] | None = None) -> list[tuple[str, str, float]]:
    """Libre Hardware Monitor 的网页服务(要在它的 Options 打开 Remote Web Server)。"""
    if fetch is None:
        def fetch(url: str) -> bytes:
            with urllib.request.urlopen(url, timeout=2) as response:  # noqa: S310 (只连本机)
                return response.read(2_000_000)
    tree = json.loads(fetch(LHM_URL))
    found = []

    def walk(node: dict, hardware: str) -> None:
        image = str(node.get("ImageURL") or "")
        text = str(node.get("Text") or "")
        if any(k in image for k in ("cpu", "nvidia", "ati", "amd", "intel", "gpu")) and node.get("Children"):
            hardware = ("gpu " if any(k in image for k in ("nvidia", "ati", "gpu")) else "cpu ") + text
        value = _value(node.get("Value")) if "°C" in str(node.get("Value") or "") else None
        if value is not None:
            found.append((text, hardware, value))
        for child in node.get("Children") or []:
            if isinstance(child, dict):
                walk(child, hardware)
    walk(tree, "")
    return found


def lhm_wmi(run: Callable[[str], str] | None = None) -> list[tuple[str, str, float]]:
    """Libre Hardware Monitor 开着时,它在 Windows 的 WMI 里放的温度。"""
    if run is None:
        def run(command: str) -> str:
            proc = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
                                  capture_output=True, timeout=20, creationflags=NO_WINDOW)
            return proc.stdout.decode("utf-8", errors="replace")
    out = run(WMI_QUERY).strip()
    if not out:
        return []
    rows = json.loads(out)
    rows = rows if isinstance(rows, list) else [rows]
    return [(str(r.get("Name") or ""), str(r.get("Parent") or r.get("Identifier") or ""), _value(r.get("Value")))
            for r in rows if isinstance(r, dict)]


def lhm_running() -> bool:
    """Libre Hardware Monitor 有没有开着(没开就不去问 WMI,免得每分钟白开一次 PowerShell)。"""
    try:
        import friday_gameboost

        return any(name.lower().startswith("librehardwaremonitor") for _, name in
                   friday_gameboost.WindowsSystem().processes())
    except Exception:
        return False


def lhm_wmi_if_running() -> list[tuple[str, str, float]]:
    return lhm_wmi() if lhm_running() else []


class Temps:
    def __init__(self, gpu: Callable[[], list[dict]] | None = None, lhm: list[Callable[[], list]] | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        self.gpu_reader = gpu
        self.lhm_readers = lhm if lhm is not None else [lhm_http, lhm_wmi_if_running]
        self.clock = clock
        self.alerted: dict[str, float] = {}
        self.last: dict = {}
        self.read_at = 0.0

    def _sensors(self) -> list:
        for reader in self.lhm_readers:
            try:
                found = reader()
            except Exception:
                continue
            if found:
                self.lhm_readers = [reader] + [r for r in self.lhm_readers if r is not reader]  # 下次先用能读的
                return found
        return []

    def read(self, max_age: float = 20) -> dict:
        """{"cpu": °C 或 None, "gpu": °C 或 None, "gpu_name": ..., "lhm": 有没有读到 Libre Hardware Monitor}。"""
        if self.last and self.clock() - self.read_at < max_age:
            return self.last
        sensors = self._sensors()
        result = {"cpu": _pick_cpu(sensors), "gpu": None, "gpu_name": "", "lhm": bool(sensors)}
        if self.gpu_reader is not None:
            try:
                gpus = self.gpu_reader()
                hot = max((g for g in gpus if g.get("temp") is not None), key=lambda g: g["temp"], default=None)
                if hot is not None:
                    result["gpu"], result["gpu_name"] = hot["temp"], hot.get("name", "")
            except Exception:
                pass
        if result["gpu"] is None:
            result["gpu"] = _pick_gpu(sensors)
        self.last, self.read_at = result, self.clock()
        return result

    def describe(self) -> str:
        t = self.read(max_age=0)
        parts = []
        if t["cpu"] is not None:
            parts.append(f"CPU {t['cpu']:.0f}°C({self._word(t['cpu'], CPU_HOT)})")
        if t["gpu"] is not None:
            parts.append(f"显卡 {t['gpu']:.0f}°C({self._word(t['gpu'], GPU_HOT)})")
        if not parts:
            return ("读不到温度。显卡温度要 NVIDIA 显卡;CPU 温度要先打开 Libre Hardware Monitor"
                    "(开着就行,在它的 Options 打开 Remote Web Server 会更快)。")
        text = "现在 " + "、".join(parts) + "。"
        if t["cpu"] is None:
            text += "CPU 温度要开着 Libre Hardware Monitor 才看得到。"
        return text

    @staticmethod
    def _word(value: float, hot: float) -> str:
        return "太热了" if value >= hot else "偏热" if value >= hot - 10 else "正常"

    def check(self, name: str) -> str | None:
        """太热就返回提醒的话(每样 30 分钟最多一次)。"""
        t = self.read()
        now = self.clock()
        said = []
        for key, label, hot in (("cpu", "CPU", CPU_HOT), ("gpu", "显卡", GPU_HOT)):
            value = t.get(key)
            if value is None or value < hot or (key in self.alerted and now - self.alerted[key] < ALERT_EVERY):
                continue
            self.alerted[key] = now
            said.append(f"{label} {value:.0f}°C")
        if not said:
            return None
        return (f"{name},{'、'.join(said)},有点太热了。看看机箱风扇、出风口有没有被挡住,"
                "或者把游戏画质、帧数上限调低一点。")
