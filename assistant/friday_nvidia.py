"""NVIDIA 显卡:温度、使用率、显存、驱动版本;打游戏时一句话存精彩片段、录像、截图(不花钱)。

状态用显卡驱动自带的 nvidia-smi 读。录像、截图用 NVIDIA App 的默认快捷键:
Alt+F10 存下刚才的精彩片段(要先在 NVIDIA App 里打开“即时重放”)、Alt+F9 开始/停止录像、Alt+F1 截图。
显卡太热(85°C 以上)会提醒一次。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Callable

QUERY = "name,temperature.gpu,utilization.gpu,memory.used,memory.total,driver_version,power.draw,fan.speed"
HOT = 85
WARM = 75
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
VK = {"alt": 0x12, "f1": 0x70, "f9": 0x78, "f10": 0x79, "z": 0x5A}
HOTKEYS = {
    "replay": (("alt", "f10"), "已经叫 NVIDIA App 存下刚才的精彩片段(没存到的话,要先在 NVIDIA App 里打开“即时重放”)。"),
    "record": (("alt", "f9"), "已经按了录像键:没在录就开始录,正在录就停下来存好(NVIDIA App 的 Alt+F9)。"),
    "screenshot": (("alt", "f1"), "已经叫 NVIDIA App 截图了(存在“视频\\NVIDIA”或你设的文件夹里)。"),
}


def find_smi() -> str | None:
    found = shutil.which("nvidia-smi")
    if found:
        return found
    for candidate in (r"C:\Windows\System32\nvidia-smi.exe",
                      r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe"):
        if Path(candidate).exists():
            return candidate
    return None


def _number(text: str) -> float | None:
    try:
        return float(text.strip())
    except (TypeError, ValueError):
        return None


def status(run: Callable[[list[str]], str] | None = None) -> list[dict]:
    """每张 NVIDIA 显卡的状态;没有 NVIDIA 显卡或驱动时抛 RuntimeError。"""
    if run is None:
        smi = find_smi()
        if smi is None:
            raise RuntimeError("这台电脑找不到 NVIDIA 显卡的驱动工具(nvidia-smi),可能不是 NVIDIA 显卡")

        def run(args: list[str]) -> str:
            proc = subprocess.run([smi, *args], capture_output=True, timeout=15, creationflags=NO_WINDOW)
            if proc.returncode != 0:
                raise RuntimeError("读不到显卡状态:" + proc.stderr.decode(errors="replace").strip()[:120])
            return proc.stdout.decode(errors="replace")
    out = run([f"--query-gpu={QUERY}", "--format=csv,noheader,nounits"])
    gpus = []
    for line in out.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 8:
            continue
        name, temp, util, used, total, driver, power, fan = parts[:8]
        gpus.append({"name": name, "temp": _number(temp), "util": _number(util), "mem_used": _number(used),
                     "mem_total": _number(total), "driver": driver, "power": _number(power), "fan": _number(fan)})
    if not gpus:
        raise RuntimeError("读不到显卡状态")
    return gpus


def describe(gpus: list[dict]) -> str:
    lines = []
    for gpu in gpus:
        parts = []
        if gpu["temp"] is not None:
            word = "很热,注意散热" if gpu["temp"] >= HOT else "偏热" if gpu["temp"] >= WARM else "正常"
            parts.append(f"温度 {gpu['temp']:.0f}°C({word})")
        if gpu["util"] is not None:
            parts.append(f"使用率 {gpu['util']:.0f}%")
        if gpu["mem_used"] is not None and gpu["mem_total"]:
            parts.append(f"显存 {gpu['mem_used'] / 1024:.1f}/{gpu['mem_total'] / 1024:.1f} GB")
        if gpu["power"] is not None:
            parts.append(f"功耗 {gpu['power']:.0f} W")
        if gpu["fan"] is not None:
            parts.append(f"风扇 {gpu['fan']:.0f}%")
        parts.append(f"驱动版本 {gpu['driver']}")
        lines.append(f"显卡 {gpu['name']}:" + ",".join(parts) + "。")
    return "\n".join(lines)


def press(keys: tuple[str, ...], send: Callable[[int, int], None] | None = None) -> None:
    """按下再放开一组键(例如 Alt+F10)。"""
    if send is None:
        if os.name != "nt":
            raise RuntimeError("只能在 Windows 上按快捷键")
        import ctypes

        user32 = ctypes.windll.user32

        def send(vk: int, flags: int) -> None:
            user32.keybd_event(vk, 0, flags, 0)
    codes = [VK[k] for k in keys]
    for code in codes:
        send(code, 0)
    time.sleep(0.05)
    for code in reversed(codes):
        send(code, 0x2)  # KEYEVENTF_KEYUP


def hotkey(action: str, send=None) -> str:
    keys, reply = HOTKEYS[action]
    press(keys, send)
    return reply


class GpuWatch:
    """每分钟看一次显卡温度,太热就提醒(30 分钟最多一次)。"""

    def __init__(self, read: Callable[[], list[dict]] = status, clock: Callable[[], float] = time.time) -> None:
        self.read = read
        self.clock = clock
        self.last_alert = 0.0

    def check(self, name: str) -> str | None:
        try:
            gpus = self.read()
        except Exception:
            return None
        hot = [g for g in gpus if g["temp"] is not None and g["temp"] >= HOT]
        if not hot or self.clock() - self.last_alert < 30 * 60:
            return None
        self.last_alert = self.clock()
        gpu = hot[0]
        return (f"{name},显卡 {gpu['name']} 现在 {gpu['temp']:.0f}°C,有点太热了。"
                "看看机箱风扇、出风口有没有被挡住,或者把游戏画质、帧数上限调低一点。")
