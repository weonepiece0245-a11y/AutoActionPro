"""桌面小功能(只在 Windows 上):全局快捷键叫出星期五并开始听,右下角托盘图标。

都不花 Claude 的钱:只是叫出窗口、开始录音,真正问问题时才和平常一样计费。
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Callable

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
VK_SPACE = 0x20
WM_HOTKEY, WM_QUIT = 0x0312, 0x0012

# 可选的快捷键(Ctrl+空格 常被输入法用来切换中英文,所以不用)
HOTKEYS = {
    "ctrl+alt+f": ("Ctrl + Alt + F", MOD_CONTROL | MOD_ALT, ord("F")),
    "ctrl+shift+space": ("Ctrl + Shift + 空格", MOD_CONTROL | MOD_SHIFT, VK_SPACE),
    "ctrl+alt+space": ("Ctrl + Alt + 空格", MOD_CONTROL | MOD_ALT, VK_SPACE),
    "win+shift+f": ("Win + Shift + F", MOD_WIN | MOD_SHIFT, ord("F")),
}
DEFAULT_HOTKEY = "ctrl+alt+f"


def hotkey_name() -> str:
    """现在设定的快捷键;off 表示关掉。"""
    value = os.environ.get("FRIDAY_HOTKEY", "").strip().lower() or DEFAULT_HOTKEY
    return value if value in HOTKEYS or value == "off" else DEFAULT_HOTKEY


def summon(connected: Callable[[], int], focus: Callable[[], bool], listen: Callable[[], None],
           open_listening: Callable[[], None]) -> str:
    """快捷键或托盘“开始说话”:有窗口就叫到前面并开始听,没有窗口就开一个(打开后自己开始听)。"""
    if connected() and focus():
        listen()
        return "focused"
    open_listening()
    return "opened"


class GlobalHotkey:
    """用 Windows 的 RegisterHotKey 注册全局快捷键(不用装别的东西)。"""

    def __init__(self, callback: Callable[[], None]) -> None:
        self.callback = callback
        self.thread: threading.Thread | None = None
        self.thread_id = 0
        self.name = "off"
        self.error = ""

    def start(self, name: str) -> bool:
        """注册快捷键;被别的程序占用了返回 False(原因在 self.error)。"""
        self.stop()
        self.name, self.error = name, ""
        if name == "off":
            return True
        if os.name != "nt" or name not in HOTKEYS:
            self.error = "只在 Windows 上可用"
            return False
        ready = threading.Event()
        result = {"ok": False}

        def loop():
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            self.thread_id = kernel32.GetCurrentThreadId()
            _, mods, vk = HOTKEYS[name]
            result["ok"] = bool(user32.RegisterHotKey(None, 1, mods | MOD_NOREPEAT, vk))
            ready.set()
            if not result["ok"]:
                return
            msg = wintypes.MSG()
            try:
                while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                    if msg.message == WM_HOTKEY:
                        try:
                            self.callback()
                        except Exception:
                            pass
            finally:
                user32.UnregisterHotKey(None, 1)

        self.thread = threading.Thread(target=loop, daemon=True, name="friday-hotkey")
        self.thread.start()
        ready.wait(5)
        if not result["ok"]:
            self.error = f"{HOTKEYS[name][0]} 已经被别的程序占用了,换一个试试"
        return result["ok"]

    def stop(self) -> None:
        if self.thread is not None and self.thread.is_alive() and os.name == "nt":
            import ctypes

            ctypes.windll.user32.PostThreadMessageW(self.thread_id, WM_QUIT, 0, 0)
            self.thread.join(2)
        self.thread = None


class TrayIcon:
    """任务栏右下角的“五”字图标:点一下打开窗口,右键有菜单。需要 pystray(没装就不显示)。"""

    def __init__(self, icon_file: Path, actions: dict[str, Callable[[], None]]) -> None:
        self.icon_file = Path(icon_file)
        self.actions = actions  # open / listen / update / quit
        self.icon = None

    def _image(self):
        from PIL import Image

        if not self.icon_file.exists():
            import friday_app

            friday_app.make_icon(self.icon_file)
        return Image.open(self.icon_file)

    def start(self) -> bool:
        if os.name != "nt":
            return False
        try:
            import pystray
        except ImportError:
            return False

        def call(name):
            return lambda icon, item: self.actions[name]()

        menu = pystray.Menu(
            pystray.MenuItem("打开星期五", call("open"), default=True),
            pystray.MenuItem("开始说话", call("listen")),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("检查更新", call("update")),
            pystray.MenuItem("退出星期五", call("quit")),
        )
        try:
            self.icon = pystray.Icon("friday", self._image(), "星期五 · 在后台运行", menu)
        except Exception:
            return False
        threading.Thread(target=self.icon.run, daemon=True, name="friday-tray").start()
        return True

    def stop(self) -> None:
        if self.icon is not None:
            try:
                self.icon.stop()
            except Exception:
                pass
            self.icon = None
