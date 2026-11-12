"""智能家居(Tapo):在家里的网络直接开关 Tapo 的灯和插座,调亮度、冷暖、颜色(不花钱)。

要在齿轮设定 → 连接的应用 里填 TP-Link / Tapo 账号的邮箱和密码(Tapo 设备要用它在家里网络里验证,
只存在这台电脑的 .env,不会发到别的地方),并在 Tapo App 里打开 我 → 第三方服务 → 第三方兼容(Third-Party
Compatibility)。用的是开源的 python-kasa(第一次用时自动安装)。Google Home、Apple Home 里的同一个设备也会跟着变。
"""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import sys
import threading
import time
from typing import Callable

REFRESH_SECONDS = 10 * 60
COLORS = {"红": (0, 100), "橙": (30, 100), "黄": (55, 100), "绿": (120, 100), "青": (180, 100), "蓝": (230, 100),
          "紫": (275, 100), "粉": (320, 60), "白": None}
ALL_WORDS = ("全部", "所有", "全屋", "家里所有", "所有的")
LIGHT_WORDS = ("灯", "电灯", "灯泡", "灯光", "lamp", "light", "bulb")
PLUG_WORDS = ("插座", "插头", "开关", "plug", "socket")


def configured() -> bool:
    return bool(os.environ.get("FRIDAY_TAPO_USER", "").strip() and os.environ.get("FRIDAY_TAPO_PASSWORD", ""))


def kasa_installed() -> bool:
    try:
        import kasa  # noqa: F401
    except ImportError:
        return False
    return True


def install_kasa(run=subprocess.run) -> bool:
    """第一次用 Tapo 时安装 python-kasa(不放进 requirements,免得装不上时拖累别的更新)。"""
    python = sys.executable
    if python.lower().endswith("pythonw.exe") and os.path.exists(python[:-5] + ".exe"):
        python = python[:-5] + ".exe"
    proc = run([python, "-m", "pip", "install", "-q", "--disable-pip-version-check", "python-kasa"],
               capture_output=True, timeout=600, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return proc.returncode == 0


class KasaBackend:
    """用 python-kasa 在家里网络里找、控制 Tapo 设备(在自己的后台线程跑异步)。"""

    def __init__(self, user: str, password: str) -> None:
        self.user, self.password = user, password
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True, name="friday-tapo").start()
        self.handles: dict[str, object] = {}

    def _run(self, coro, timeout: float = 25):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    def discover(self) -> list[dict]:
        from kasa import Credentials, Discover

        async def find():
            found = await Discover.discover(credentials=Credentials(self.user, self.password), discovery_timeout=5)
            items = []
            for host, dev in found.items():
                try:
                    await dev.update()
                except Exception:
                    continue
                children = list(getattr(dev, "children", []) or [])
                for index, target in enumerate(children or [dev]):
                    key = f"{host}#{index}" if children else host
                    self.handles[key] = target
                    items.append(self._info(key, target))
            return items

        return self._run(find(), timeout=30)

    @staticmethod
    def _light(dev):
        try:
            from kasa import Module

            return dev.modules.get(Module.Light)
        except Exception:
            return None

    def _info(self, key: str, dev) -> dict:
        light = self._light(dev)
        brightness = hsv = color_temp = None
        if light is not None:
            try:
                brightness = light.brightness
            except Exception:
                brightness = None
            try:  # 记下颜色,游戏联动打完时改得回来
                if getattr(light, "is_color", False):
                    h, s, v = light.hsv
                    hsv = [int(h), int(s), int(v)]
                if getattr(light, "is_variable_color_temp", False) and light.color_temp:
                    color_temp = int(light.color_temp)
            except Exception:
                pass
        return {"id": key, "name": str(getattr(dev, "alias", "") or key), "on": bool(getattr(dev, "is_on", False)),
                "kind": "light" if light is not None else "plug", "brightness": brightness, "hsv": hsv,
                "color_temp": color_temp}

    def set_on(self, key: str, on: bool) -> None:
        dev = self.handles[key]
        self._run(dev.turn_on() if on else dev.turn_off())

    def set_brightness(self, key: str, value: int) -> None:
        light = self._light(self.handles[key])
        if light is None:
            raise ValueError("这个设备不能调亮度")
        self._run(light.set_brightness(int(value)))

    def set_color_temp(self, key: str, kelvin: int) -> None:
        light = self._light(self.handles[key])
        if light is None:
            raise ValueError("这个设备不能调冷暖")
        self._run(light.set_color_temp(int(kelvin)))

    def set_hsv(self, key: str, hue: int, saturation: int, value: int) -> None:
        light = self._light(self.handles[key])
        if light is None:
            raise ValueError("这个设备不能调颜色")
        self._run(light.set_hsv(int(hue), int(saturation), int(value)))


class TapoHome:
    def __init__(self, backend_factory: Callable[[], object], clock: Callable[[], float] = time.time) -> None:
        self.backend_factory = backend_factory
        self.backend = None
        self.clock = clock
        self.devices: list[dict] = []
        self.refreshed = 0.0
        self.lock = threading.Lock()

    def refresh(self, force: bool = False) -> list[dict]:
        if not force and self.devices and self.clock() - self.refreshed >= REFRESH_SECONDS and not self.lock.locked():
            threading.Thread(target=lambda: self._quiet_refresh(), daemon=True).start()  # 旧的先用着,后台再找一次
            return self.devices
        with self.lock:
            if not force and self.devices:
                return self.devices
            if self.backend is None:
                self.backend = self.backend_factory()
            try:
                self.devices = self.backend.discover()
            except ImportError:
                raise ValueError("控制 Tapo 的组件还没装好,等一下再试(第一次要装一两分钟)") from None
            except Exception as e:  # noqa: BLE001
                text = str(e).lower()
                if "auth" in text or "credential" in text or "login" in text:
                    raise ValueError("Tapo 账号或密码不对;也看看 Tapo App 里 我 → 第三方服务 → 第三方兼容 有没有打开") from None
                raise ValueError("找 Tapo 设备时出错了,电脑和设备要连同一个 Wi-Fi") from None
            self.refreshed = self.clock()
            return self.devices

    def _quiet_refresh(self) -> None:
        try:
            self.refresh(force=True)
        except Exception:
            pass

    def match(self, words: str) -> list[dict]:
        """“客厅灯”“灯”“插座”“全部”“卧室” -> 对应的设备。"""
        words = re.sub(r"\s+", "", str(words or "")).strip("的把")
        devices = self.refresh()
        if not devices:
            return []
        if not words or any(w in words for w in ALL_WORDS) and not any(w in words for w in LIGHT_WORDS + PLUG_WORDS):
            return list(devices) if words else []
        lower = words.lower()
        named = [d for d in devices if d["name"].lower() in lower or lower in d["name"].lower()]
        if named:
            return named
        if any(w in lower for w in LIGHT_WORDS):
            lights = [d for d in devices if d["kind"] == "light"]
            return lights if (any(w in words for w in ALL_WORDS) or len(lights) == 1 or words in LIGHT_WORDS) else []
        if any(w in lower for w in PLUG_WORDS):
            plugs = [d for d in devices if d["kind"] == "plug"]
            return plugs if (any(w in words for w in ALL_WORDS) or len(plugs) == 1 or words in PLUG_WORDS) else []
        return []

    @staticmethod
    def _names(targets: list[dict]) -> str:
        return "、".join(d["name"] for d in targets[:6]) + ("……" if len(targets) > 6 else "")

    def _each(self, targets: list[dict], action: Callable[[dict], None]) -> list[str]:
        failed = []
        for device in targets:
            try:
                action(device)
            except Exception:
                failed.append(device["name"])
        return failed

    def switch(self, words: str, on: bool) -> str | None:
        targets = self.match(words)
        if not targets:
            return None
        failed = self._each(targets, lambda d: self.backend.set_on(d["id"], on))
        for d in targets:
            if d["name"] not in failed:
                d["on"] = on
        done = [d for d in targets if d["name"] not in failed]
        text = (f"已{'打开' if on else '关掉'}:{self._names(done)}。") if done else ""
        return text + (f"{'、'.join(failed)} 没反应(看看它有没有断网)。" if failed else "")

    def brightness(self, words: str, value: int | None = None, step: int = 0) -> str | None:
        targets = [d for d in self.match(words or "灯") if d["kind"] == "light"]
        if not targets:
            return None

        def apply(device: dict) -> None:
            current = device.get("brightness") or 50
            new = max(1, min(100, value if value is not None else current + step))
            if not device.get("on"):
                self.backend.set_on(device["id"], True)
                device["on"] = True
            self.backend.set_brightness(device["id"], new)
            device["brightness"] = new
        failed = self._each(targets, apply)
        done = [d for d in targets if d["name"] not in failed]
        if not done:
            return "调不了亮度(这个灯可能不支持,或者断网了)。"
        return f"{self._names(done)} 亮度调到 {done[0]['brightness']}%。"

    def warmth(self, words: str, warm: bool) -> str | None:
        targets = [d for d in self.match(words or "灯") if d["kind"] == "light"]
        if not targets:
            return None
        failed = self._each(targets, lambda d: self.backend.set_color_temp(d["id"], 2700 if warm else 6000))
        done = [d for d in targets if d["name"] not in failed]
        return (f"{self._names(done)} 换成{'暖' if warm else '白'}光了。" if done
                else "调不了冷暖(这个灯可能只有一种颜色)。")

    def color(self, words: str, color: str) -> str | None:
        targets = [d for d in self.match(words or "灯") if d["kind"] == "light"]
        if not targets or color not in COLORS:
            return None
        hsv = COLORS[color]
        if hsv is None:
            return self.warmth(words or "灯", warm=False)
        failed = self._each(targets, lambda d: self.backend.set_hsv(d["id"], hsv[0], hsv[1], d.get("brightness") or 80))
        done = [d for d in targets if d["name"] not in failed]
        return f"{self._names(done)} 换成{color}色了。" if done else "调不了颜色(这个灯可能不是彩色灯)。"

    def describe(self, force: bool = True) -> str:
        devices = self.refresh(force=force)
        if not devices:
            return ("在家里的网络里没找到 Tapo 设备。电脑和设备要连同一个 Wi-Fi,"
                    "Tapo App 里 我 → 第三方服务 → 第三方兼容 要打开。")
        parts = []
        for d in devices:
            state = "开" if d["on"] else "关"
            if d["kind"] == "light" and d["on"] and d.get("brightness"):
                state += f",亮度 {d['brightness']}%"
            parts.append(f"{d['name']}({state})")
        return f"家里有 {len(devices)} 个 Tapo 设备:" + "、".join(parts) + "。"
