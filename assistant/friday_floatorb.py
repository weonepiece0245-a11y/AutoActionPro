"""桌面小光球:只有一颗会发光的小球浮在桌面最上面,球以外完全透明(点到透明的地方会直接点到后面的程序)。

- 点一下:开始说话(窗口里的星期五会开始听);再点一下:停。
- 按住拖动:换位置(会记住)。
- 右键:收起小光球。
- 颜色跟着窗口的主题;在听、在想、在说时,光球的动法不一样。
只在 Windows 上有(用 Windows 自带的透明窗口,不用装别的)。
"""

from __future__ import annotations

import json
import math
import os
import re
import threading
import time
from pathlib import Path
from typing import Callable

SIZE = 132  # 窗口大小(像素),光球在正中间
RADIUS = 30
FPS = 15
FRAMES = 30  # 一圈动画几帧
DRAG_PIXELS = 4


# ======================== 画光球(哪个系统都能跑,方便测试) ========================


def parse_color(text: str, default=(34, 211, 238)) -> tuple[int, int, int]:
    text = str(text or "").strip()
    found = re.fullmatch(r"#?([0-9a-fA-F]{6})", text)
    if found:
        value = found.group(1)
        return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    found = re.match(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)", text)
    if found:
        return tuple(min(255, int(found.group(i))) for i in (1, 2, 3))  # type: ignore[return-value]
    return default


def render_frame(color: tuple[int, int, int], mode: str, phase: float):
    """一帧光球(RGBA,背景完全透明)。phase:0~1,动画走到哪里。"""
    from PIL import Image, ImageChops, ImageDraw, ImageFilter

    wave = 0.5 + 0.5 * math.sin(phase * 2 * math.pi)
    scale, glow = 1.0, 0.55
    if mode == "idle":
        scale, glow = 0.94 + 0.06 * wave, 0.45 + 0.2 * wave
    elif mode == "listening":
        scale, glow = 0.9 + 0.14 * wave, 0.65 + 0.35 * wave
    elif mode == "speaking":
        scale, glow = 0.96 + 0.08 * abs(math.sin(phase * 6 * math.pi)), 0.7 + 0.25 * wave
    elif mode == "thinking":
        scale, glow = 0.97, 0.6
    radius = RADIUS * scale
    cx = cy = SIZE / 2
    r, g, b = color

    # 外面的光晕
    halo = Image.new("RGBA", (SIZE, SIZE), (r, g, b, 0))
    draw = ImageDraw.Draw(halo)
    outer = radius * 1.55
    draw.ellipse([cx - outer, cy - outer, cx + outer, cy + outer], fill=(r, g, b, int(150 * glow)))
    halo = halo.filter(ImageFilter.GaussianBlur(radius * 0.45))

    # 球:从边缘的颜色到亮点的白色,一圈一圈画
    ball = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(ball)
    angle = -math.pi * 0.75 + (phase * 2 * math.pi if mode == "thinking" else 0)  # 想事情时亮点转圈
    hx, hy = cx + math.cos(angle) * radius * 0.35, cy + math.sin(angle) * radius * 0.35
    steps = 28
    for i in range(steps):
        t = i / (steps - 1)  # 0 外圈 -> 1 亮点
        rr = radius * (1 - t * 0.92)
        px, py = cx + (hx - cx) * t, cy + (hy - cy) * t
        edge = 0.62  # 外圈暗一点,有立体感
        mix = t ** 1.6
        col = tuple(int(c * (edge + (1 - edge) * min(1, t * 1.6)) * (1 - mix) + 255 * mix) for c in color)
        draw.ellipse([px - rr, py - rr, px + rr, py + rr], fill=(*col, 255))
    ball = ball.filter(ImageFilter.GaussianBlur(0.8))
    mask = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(mask).ellipse([cx - radius, cy - radius, cx + radius, cy + radius], fill=255)
    ball.putalpha(ImageChops.multiply(ball.getchannel("A"), mask.filter(ImageFilter.GaussianBlur(0.7))))
    return Image.alpha_composite(halo, ball)


def premultiplied_bgra(image) -> bytes:
    """Windows 透明窗口要的格式:每个点 B G R A,颜色先乘过透明度。"""
    from PIL import Image, ImageChops

    r, g, b, a = image.split()
    r, g, b = (ImageChops.multiply(c, a) for c in (r, g, b))
    return Image.merge("RGBA", (b, g, r, a)).tobytes()


class Frames:
    """先把每种状态的动画画好存起来(换颜色才重画),不用每一帧都现画。"""

    def __init__(self) -> None:
        self.cache: dict[tuple, list[bytes]] = {}

    def get(self, color: tuple[int, int, int], mode: str, index: int) -> bytes:
        key = (color, mode)
        if key not in self.cache:
            if len(self.cache) > 12:
                self.cache.clear()
            self.cache[key] = [premultiplied_bgra(render_frame(color, mode, i / FRAMES)) for i in range(FRAMES)]
        frames = self.cache[key]
        return frames[index % len(frames)]


# ======================== Windows 透明窗口 ========================


class FloatOrb:
    def __init__(self, state_path: str | os.PathLike, mode: Callable[[], str] = lambda: "idle",
                 color: Callable[[], str] = lambda: "#22d3ee", on_click: Callable[[], None] = lambda: None,
                 on_close: Callable[[], None] = lambda: None) -> None:
        self.state_path = Path(state_path)
        self.mode = mode
        self.color = color
        self.on_click = on_click
        self.on_close = on_close
        self.frames = Frames()
        self.hwnd = None
        self.thread: threading.Thread | None = None
        self.x, self.y = self._load_position()
        self.index = 0
        self._press = None  # 按下去的位置(看是点一下还是拖动)
        self.dragging = False

    @property
    def running(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def _load_position(self) -> tuple[int | None, int | None]:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            return int(data["x"]), int(data["y"])
        except (OSError, ValueError, KeyError, TypeError):
            return None, None

    def _save_position(self) -> None:
        try:
            self.state_path.write_text(json.dumps({"x": self.x, "y": self.y}), encoding="utf-8")
        except OSError:
            pass

    def start(self) -> bool:
        if os.name != "nt":
            return False
        if self.running:
            return True
        ready = threading.Event()
        self.thread = threading.Thread(target=self._run, args=(ready,), daemon=True, name="friday-floatorb")
        self.thread.start()
        ready.wait(5)
        return self.running

    def stop(self) -> None:
        if self.hwnd and self.running:
            import ctypes

            ctypes.windll.user32.PostMessageW(self.hwnd, 0x0010, 0, 0)  # WM_CLOSE

    # ---- 窗口线程 ----
    def _run(self, ready: threading.Event) -> None:
        import ctypes
        from ctypes import wintypes

        user32, gdi32, kernel32 = ctypes.windll.user32, ctypes.windll.gdi32, ctypes.windll.kernel32
        LRESULT = ctypes.c_ssize_t
        WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

        class WNDCLASSEXW(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT), ("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
                        ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE),
                        ("hIcon", wintypes.HICON), ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                        ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR), ("hIconSm", wintypes.HICON)]

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                        ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                        ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]

        class BLENDFUNCTION(ctypes.Structure):
            _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                        ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]

        user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.DefWindowProcW.restype = LRESULT
        user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                           ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                           wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.LoadCursorW.argtypes = [wintypes.HINSTANCE, wintypes.LPVOID]
        user32.LoadCursorW.restype = wintypes.HANDLE
        user32.UpdateLayeredWindow.argtypes = [wintypes.HWND, wintypes.HDC, ctypes.POINTER(wintypes.POINT),
                                               ctypes.POINTER(wintypes.SIZE), wintypes.HDC, ctypes.POINTER(wintypes.POINT),
                                               wintypes.COLORREF, ctypes.POINTER(BLENDFUNCTION), wintypes.DWORD]
        user32.GetDC.restype = wintypes.HDC
        user32.GetDC.argtypes = [wintypes.HWND]
        user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
        user32.SetTimer.argtypes = [wintypes.HWND, ctypes.c_size_t, wintypes.UINT, wintypes.LPVOID]
        user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        gdi32.CreateCompatibleDC.restype = wintypes.HDC
        gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
        gdi32.CreateDIBSection.restype = wintypes.HBITMAP
        gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.c_void_p, wintypes.UINT,
                                           ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD]
        gdi32.SelectObject.restype = wintypes.HGDIOBJ
        gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
        gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
        gdi32.DeleteDC.argtypes = [wintypes.HDC]
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE

        if self.x is None:  # 第一次:放在右下角、工作列上面一点
            self.x = user32.GetSystemMetrics(0) - SIZE - 40
            self.y = user32.GetSystemMetrics(1) - SIZE - 120

        screen_dc = user32.GetDC(None)
        mem_dc = gdi32.CreateCompatibleDC(screen_dc)
        info = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), SIZE, -SIZE, 1, 32, 0, 0, 0, 0, 0, 0)
        bits = ctypes.c_void_p()
        bitmap = gdi32.CreateDIBSection(mem_dc, ctypes.byref(info), 0, ctypes.byref(bits), None, 0)
        old_bitmap = gdi32.SelectObject(mem_dc, bitmap)
        blend = BLENDFUNCTION(0, 0, 255, 1)  # AC_SRC_OVER, 用每个点自己的透明度
        size = wintypes.SIZE(SIZE, SIZE)
        source = wintypes.POINT(0, 0)

        def draw(hwnd) -> None:
            try:
                color = parse_color(self.color())
                mode = self.mode() or "idle"
            except Exception:
                color, mode = parse_color(""), "idle"
            data = self.frames.get(color, mode, self.index)
            ctypes.memmove(bits, data, len(data))
            where = wintypes.POINT(int(self.x), int(self.y))
            user32.UpdateLayeredWindow(hwnd, screen_dc, ctypes.byref(where), ctypes.byref(size), mem_dc,
                                       ctypes.byref(source), 0, ctypes.byref(blend), 2)  # ULW_ALPHA

        def cursor() -> tuple[int, int]:
            point = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(point))
            return point.x, point.y

        def proc(hwnd, msg, wparam, lparam):
            if msg == 0x0113:  # WM_TIMER:下一帧
                self.index += 1
                draw(hwnd)
                return 0
            if msg == 0x0201:  # 左键按下
                self._press = (*cursor(), self.x, self.y)
                self.dragging = False
                user32.SetCapture(hwnd)
                return 0
            if msg == 0x0200 and self._press is not None:  # 拖动
                cx, cy = cursor()
                px, py, ox, oy = self._press
                if self.dragging or abs(cx - px) > DRAG_PIXELS or abs(cy - py) > DRAG_PIXELS:
                    self.dragging = True
                    self.x, self.y = ox + cx - px, oy + cy - py
                    draw(hwnd)
                return 0
            if msg == 0x0202:  # 左键放开
                user32.ReleaseCapture()
                pressed, self._press = self._press, None
                if self.dragging:
                    self._save_position()
                elif pressed is not None:
                    threading.Thread(target=self.on_click, daemon=True).start()
                self.dragging = False
                return 0
            if msg == 0x0205:  # 右键:收起
                user32.PostMessageW(hwnd, 0x0010, 0, 0)
                return 0
            if msg == 0x0010:  # WM_CLOSE
                user32.DestroyWindow(hwnd)
                return 0
            if msg == 0x0002:  # WM_DESTROY
                user32.PostQuitMessage(0)
                return 0
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        self._proc = WNDPROC(proc)  # 留着引用,不然会被回收
        instance = kernel32.GetModuleHandleW(None)
        name = f"FridayFloatOrb{id(self)}"
        wc = WNDCLASSEXW(ctypes.sizeof(WNDCLASSEXW), 0, self._proc, 0, 0, instance, None,
                         user32.LoadCursorW(None, ctypes.c_void_p(32649)), None, None, name, None)  # 手形光标
        user32.RegisterClassExW(ctypes.byref(wc))
        # WS_EX_LAYERED | WS_EX_TOPMOST | WS_EX_TOOLWINDOW(不出现在工作列)| WS_EX_NOACTIVATE(点它不会抢走焦点)
        hwnd = user32.CreateWindowExW(0x00080000 | 0x00000008 | 0x00000080 | 0x08000000, name, "星期五",
                                      0x80000000, int(self.x), int(self.y), SIZE, SIZE, None, None, instance, None)
        if not hwnd:
            ready.set()
            return
        self.hwnd = hwnd
        draw(hwnd)
        user32.ShowWindow(hwnd, 4)  # SW_SHOWNOACTIVATE
        user32.SetTimer(hwnd, 1, int(1000 / FPS), None)
        ready.set()
        msg = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            gdi32.SelectObject(mem_dc, old_bitmap)
            gdi32.DeleteObject(bitmap)
            gdi32.DeleteDC(mem_dc)
            user32.ReleaseDC(None, screen_dc)
            user32.UnregisterClassW(name, instance)
            self.hwnd = None
            try:
                self.on_close()
            except Exception:
                pass
