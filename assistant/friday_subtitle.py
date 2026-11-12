"""游戏里显示字幕:按住说话问的问题,星期五的回答除了读出来,也在画面下方显示几秒(半透明、点不到、不抢焦点)。

在自己的小程序里跑(和桌面小光球一样),卡住也不会拖住星期五;星期五关掉它也会自己结束。
无边框窗口 / 视窗模式的游戏看得到;少数“全屏独占”的游戏会盖过所有窗口,那就看不到(改成无边框全屏就好)。
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Callable

MAX_WIDTH = 760
FONT_SIZE = 22
PADDING = 16
MAX_LINES = 6
MAX_CHARS = 240
POLL_SECONDS = 0.25
GIVE_UP_SECONDS = 30


def show_seconds(text: str) -> float:
    """字越多显示越久:4 秒起,最多 20 秒。"""
    return min(20.0, 4.0 + len(text) * 0.18)


def shorten(text: str) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= MAX_CHARS else text[: MAX_CHARS - 1] + "…"


def _font():
    from PIL import ImageFont

    for name in ("msyh.ttc", "msyhbd.ttc", "simhei.ttf", "NotoSansCJK-Regular.ttc", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, FONT_SIZE)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=FONT_SIZE)
    except TypeError:
        return ImageFont.load_default()


def wrap(text: str, measure: Callable[[str], float], width: int) -> list[str]:
    """按画面宽度换行(中文一个字一个字量,英文尽量不拆词);太长的最后一行加“…”。"""
    lines, line, used = [], "", 0
    for char in text:
        if measure(line + char) <= width:
            line += char
            used += 1
            continue
        if len(lines) + 1 >= MAX_LINES:
            break
        if char.isascii() and char != " " and " " in line[-12:] and line[-1:].isascii():
            head, _, tail = line.rpartition(" ")
            lines.append(head)
            line = tail + char
        else:
            lines.append(line)
            line = "" if char == " " else char
        used += 1
    if line:
        lines.append(line)
    if used < len(text) and lines:  # 没放完
        last = lines[-1]
        while last and measure(last + "…") > width:
            last = last[:-1]
        lines[-1] = last + "…"
    return lines


def render(text: str, accent: tuple[int, int, int] = (34, 211, 238)):
    """一张半透明的字幕图(RGBA)。"""
    from PIL import Image, ImageDraw

    font = _font()
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    measure = lambda s: probe.textlength(s, font=font)  # noqa: E731
    lines = wrap(shorten(text), measure, MAX_WIDTH - PADDING * 2) or [""]
    line_height = int(FONT_SIZE * 1.45)
    width = int(min(MAX_WIDTH, max(measure(line) for line in lines) + PADDING * 2 + 10))
    height = line_height * len(lines) + PADDING * 2
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((0, 0, width - 1, height - 1), radius=14, fill=(4, 12, 24, 205), outline=accent + (200,), width=2)
    draw.rectangle((0, 12, 4, height - 12), fill=accent + (255,))
    for i, line in enumerate(lines):
        y = PADDING + i * line_height
        draw.text((PADDING + 8 + 1, y + 1), line, font=font, fill=(0, 0, 0, 200))  # 一点阴影,亮的画面也看得清
        draw.text((PADDING + 8, y), line, font=font, fill=(245, 250, 255, 255))
    return image


# ======================== 窗口(只有 Windows) ========================


class SubtitleWindow:
    def __init__(self) -> None:
        import friday_floatorb

        self.win = friday_floatorb._win32()
        user32 = self.win["user32"]
        ex_style = 0x80000 | 0x20 | 0x8 | 0x80 | 0x8000000  # 分层、点不到、置顶、不进工作列、不抢焦点
        self.hwnd = user32.CreateWindowExW(ex_style, friday_floatorb.CLASS_NAME, "星期五字幕", 0x80000000,
                                           0, 0, 10, 10, None, None, self.win["instance"], None)
        self.screen_dc = user32.GetDC(None)
        self.mem_dc = self.win["gdi32"].CreateCompatibleDC(self.screen_dc)
        self.visible = False

    def show(self, image) -> None:
        win = self.win
        ctypes, wintypes, user32, gdi32 = win["ctypes"], win["wintypes"], win["user32"], win["gdi32"]
        import friday_floatorb

        width, height = image.size
        info = win["BITMAPINFOHEADER"](ctypes.sizeof(win["BITMAPINFOHEADER"]), width, -height, 1, 32, 0, 0, 0, 0, 0, 0)
        bits = ctypes.c_void_p()
        bitmap = gdi32.CreateDIBSection(self.mem_dc, ctypes.byref(info), 0, ctypes.byref(bits), None, 0)
        old = gdi32.SelectObject(self.mem_dc, bitmap)
        data = friday_floatorb.premultiplied_bgra(image)
        ctypes.memmove(bits, data, len(data))
        screen_w, screen_h = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
        where = wintypes.POINT((screen_w - width) // 2, int(screen_h * 0.78) - height)  # 画面下方中间
        size, source = wintypes.SIZE(width, height), wintypes.POINT(0, 0)
        blend = win["BLENDFUNCTION"](0, 0, 255, 1)
        user32.UpdateLayeredWindow(self.hwnd, self.screen_dc, ctypes.byref(where), ctypes.byref(size), self.mem_dc,
                                   ctypes.byref(source), 0, ctypes.byref(blend), 2)
        user32.SetWindowPos(self.hwnd, wintypes.HWND(-1), 0, 0, 0, 0, 0x1 | 0x2 | 0x10 | 0x40)  # 置顶、不抢焦点、显示
        gdi32.SelectObject(self.mem_dc, old)
        gdi32.DeleteObject(bitmap)
        self.visible = True

    def hide(self) -> None:
        if self.visible:
            self.win["user32"].ShowWindow(self.hwnd, 0)
            self.visible = False

    def pump(self) -> None:
        """处理窗口消息(不处理的话 Windows 会觉得它“没有响应”)。"""
        ctypes, wintypes, user32 = self.win["ctypes"], self.win["wintypes"], self.win["user32"]
        msg = wintypes.MSG()
        while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):  # PM_REMOVE
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))


def run_process(folder: Path, parent: int | None = None, connect=None, parent_alive=None, window=None,
                sleep=time.sleep, clock=time.monotonic) -> None:
    """字幕小程序:每 0.25 秒问星期五有没有新字幕;星期五不在了就结束。"""
    import friday_floatorb

    connect = connect or (lambda: friday_floatorb._remote(folder, parent))
    if parent_alive is None:
        import friday_guard

        parent_alive = (lambda: friday_guard.pid_alive(parent)) if parent else (lambda: True)
    window = window or SubtitleWindow()
    link = {"call": None}
    shown_id, hide_at, last_ok = None, 0.0, clock()
    while True:
        window.pump()
        try:
            if link["call"] is None:
                link["call"] = connect()
            data = link["call"]("GET", "/api/subtitle", None)
            last_ok = clock()
            if data.get("id") and data.get("id") != shown_id and data.get("text"):
                shown_id = data["id"]
                accent = friday_floatorb.parse_color(data.get("accent") or "")
                window.show(render(data["text"], accent))
                hide_at = clock() + show_seconds(data["text"])
        except Exception:
            link["call"] = None
            if clock() - last_ok > GIVE_UP_SECONDS or not parent_alive():
                window.hide()
                return
        if window.visible and clock() >= hide_at:
            window.hide()
        sleep(POLL_SECONDS)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        run_process(Path(sys.argv[1]), int(sys.argv[2]) if len(sys.argv) > 2 else None)
