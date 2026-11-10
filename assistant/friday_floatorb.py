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
FPS = 24
FRAMES = 48  # 一圈动画几帧(2 秒)
LIVE_MODES = ("listening", "speaking")  # 跟着声音动:每一帧现画(一帧只要两三毫秒)
CHECK_EVERY = 48  # 每 2 秒检查一次:有没有被收起来、被盖住
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


def render_frame(color: tuple[int, int, int], mode: str, phase: float, level: float = 0.0):
    """一帧光球(RGBA,背景完全透明)。phase:0~1,动画走到哪里;level:0~1,现在的声音有多大。"""
    from PIL import Image, ImageChops, ImageDraw, ImageFilter

    two_pi = 2 * math.pi
    wave = 0.5 + 0.5 * math.sin(phase * two_pi)
    level = max(0.0, min(1.0, level))
    r, g, b = color
    cx = cy = SIZE / 2
    squash = 0.0  # 说话时像果冻一样一扁一圆
    rings: list[tuple[float, float]] = []  # (半径倍数, 透明度):往外散的声波
    dots = 0  # 想事情时绕着转的小点
    if mode == "idle":
        scale, glow = 0.94 + 0.05 * wave, 0.42 + 0.18 * wave
        cy += 2.5 * math.sin(phase * two_pi)  # 慢慢上下漂
    elif mode == "listening":
        lively = max(level, 0.15 + 0.15 * wave)
        scale, glow = 0.92 + 0.16 * lively, 0.6 + 0.4 * lively
        rings = [(1.25 + 0.12 * math.sin(phase * two_pi * 2), 90 + 120 * lively)]  # 一圈在“收音”的环
        cy += 1.5 * math.sin(phase * two_pi)
    elif mode == "speaking":
        lively = max(level, 0.25 + 0.25 * abs(math.sin(phase * two_pi * 3)))
        scale, glow = 0.93 + 0.17 * lively, 0.65 + 0.35 * lively
        squash = 0.08 * lively * math.sin(phase * two_pi * 4)
        for k in range(3):  # 三圈声波一直往外散
            progress = (phase * 2 + k / 3) % 1
            rings.append((1.05 + 0.95 * progress, (1 - progress) * (70 + 150 * lively)))
    else:  # thinking
        scale, glow = 0.97, 0.6
        dots = 3
    radius = RADIUS * scale
    rx, ry = radius * (1 + squash), radius * (1 - squash)

    # 外面的光晕
    halo = Image.new("RGBA", (SIZE, SIZE), (r, g, b, 0))
    draw = ImageDraw.Draw(halo)
    outer = radius * 1.55
    draw.ellipse([cx - outer, cy - outer, cx + outer, cy + outer], fill=(r, g, b, int(150 * glow)))
    halo = halo.filter(ImageFilter.GaussianBlur(radius * 0.45))
    if rings:
        wave_layer = Image.new("RGBA", (SIZE, SIZE), (r, g, b, 0))
        draw = ImageDraw.Draw(wave_layer)
        for factor, alpha in rings:
            ring = radius * factor
            if ring * 2 < SIZE - 2:
                draw.ellipse([cx - ring, cy - ring, cx + ring, cy + ring], outline=(r, g, b, int(min(255, alpha))), width=2)
        halo = Image.alpha_composite(halo, wave_layer.filter(ImageFilter.GaussianBlur(0.8)))
    if dots:
        dot_layer = Image.new("RGBA", (SIZE, SIZE), (r, g, b, 0))
        draw = ImageDraw.Draw(dot_layer)
        for k in range(dots):
            angle = phase * two_pi * 1.5 + k * two_pi / dots
            px, py = cx + math.cos(angle) * radius * 1.5, cy + math.sin(angle) * radius * 1.5
            size = 3.2 - k * 0.6
            dot = tuple(int(c * 0.55 + 255 * 0.45) for c in color)  # 带一点主题颜色,浅色桌面上也看得到
            draw.ellipse([px - size, py - size, px + size, py + size], fill=(*dot, 240))
        halo = Image.alpha_composite(halo, dot_layer.filter(ImageFilter.GaussianBlur(0.6)))

    # 球:从边缘的颜色到亮点的白色,一圈一圈画
    ball = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(ball)
    angle = -math.pi * 0.75 + (phase * two_pi if mode == "thinking" else 0)  # 想事情时亮点转圈
    hx, hy = cx + math.cos(angle) * radius * 0.35, cy + math.sin(angle) * radius * 0.35
    steps = 28
    for i in range(steps):
        t = i / (steps - 1)  # 0 外圈 -> 1 亮点
        sx, sy = rx * (1 - t * 0.92), ry * (1 - t * 0.92)
        px, py = cx + (hx - cx) * t, cy + (hy - cy) * t
        edge = 0.62  # 外圈暗一点,有立体感
        mix = t ** 1.6
        col = tuple(int(c * (edge + (1 - edge) * min(1, t * 1.6)) * (1 - mix) + 255 * mix) for c in color)
        draw.ellipse([px - sx, py - sy, px + sx, py + sy], fill=(*col, 255))
    ball = ball.filter(ImageFilter.GaussianBlur(0.8))
    mask = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(mask).ellipse([cx - rx, cy - ry, cx + rx, cy + ry], fill=255)
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

    def get(self, color: tuple[int, int, int], mode: str, index: int, level: float | None = None) -> bytes:
        if mode in LIVE_MODES:  # 在听、在说:跟着声音大小现画
            return premultiplied_bgra(render_frame(color, mode, (index % FRAMES) / FRAMES, level or 0.0))
        key = (color, mode)
        if key not in self.cache:
            if len(self.cache) > 12:
                self.cache.clear()
            self.cache[key] = [premultiplied_bgra(render_frame(color, mode, i / FRAMES)) for i in range(FRAMES)]
        frames = self.cache[key]
        return frames[index % len(frames)]


# ======================== Windows 透明窗口 ========================

MENU_TALK, MENU_OPEN, MENU_HIDE = 1, 2, 3
_WIN: dict = {}  # Windows 的函数和结构(第一次用时准备好,整个星期五只准备一次)
_HANDLERS: dict[int, Callable] = {}  # 每个小光球窗口 -> 处理消息的函数
_CLASS_LOCK = threading.Lock()
CLASS_NAME = "FridayFloatOrb"


def _win32() -> dict:
    """准备 Windows 的函数、结构,并且只登记一次窗口类别。

    以前每次打开小光球都重新登记、收起时注销,第二次打开时 Windows 还拿着第一次的旧函数,就会“没有响应”。
    现在整个星期五只登记一次,处理消息的函数也一直留着,开关多少次都没事。
    """
    with _CLASS_LOCK:
        if _WIN:
            return _WIN
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
        user32.KillTimer.argtypes = [wintypes.HWND, ctypes.c_size_t]
        user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                        ctypes.c_int, wintypes.UINT]
        user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
        user32.MonitorFromPoint.restype = wintypes.HANDLE
        user32.CreatePopupMenu.restype = wintypes.HMENU
        user32.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR]
        user32.TrackPopupMenu.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                          wintypes.HWND, wintypes.LPVOID]
        user32.TrackPopupMenu.restype = ctypes.c_int
        user32.DestroyMenu.argtypes = [wintypes.HMENU]
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

        def dispatch(hwnd, msg, wparam, lparam):
            handler = _HANDLERS.get(int(hwnd or 0))
            if handler is not None:
                try:
                    result = handler(hwnd, msg, wparam, lparam)
                except Exception:  # 出错也不能让小光球卡死
                    result = None
                if result is not None:
                    return result
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        proc = WNDPROC(dispatch)  # 一直留着,不会被回收
        instance = kernel32.GetModuleHandleW(None)
        wc = WNDCLASSEXW(ctypes.sizeof(WNDCLASSEXW), 0, proc, 0, 0, instance, None,
                         user32.LoadCursorW(None, ctypes.c_void_p(32649)), None, None, CLASS_NAME, None)  # 手形光标
        user32.RegisterClassExW(ctypes.byref(wc))  # 已经登记过(1410)也没关系
        _WIN.update(ctypes=ctypes, wintypes=wintypes, user32=user32, gdi32=gdi32, instance=instance, proc=proc,
                    BITMAPINFOHEADER=BITMAPINFOHEADER, BLENDFUNCTION=BLENDFUNCTION)
        return _WIN


class FloatOrb:
    def __init__(self, state_path: str | os.PathLike, mode: Callable[[], str] = lambda: "idle",
                 color: Callable[[], str] = lambda: "#22d3ee", on_click: Callable[[], None] = lambda: None,
                 on_close: Callable[[], None] = lambda: None, level: Callable[[], float | None] = lambda: None,
                 on_open: Callable[[], None] = lambda: None) -> None:
        self.state_path = Path(state_path)
        self.mode = mode
        self.level = level  # 现在的声音有多大(窗口传来的,0~1;没有就是 None)
        self.color = color
        self.on_click = on_click  # 点一下:说话
        self.on_open = on_open  # 右键选单“打开星期五窗口”
        self.on_close = on_close  # 用右键选单收起来了
        self.frames = Frames()
        self.hwnd = None
        self.thread: threading.Thread | None = None
        self.x, self.y = self._load_position()
        self.index = 0
        self._press = None  # 按下去的位置(看是点一下还是拖动)
        self.dragging = False
        self.closed_by_menu = False

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
        self.closed_by_menu = False
        self.thread = threading.Thread(target=self._run, args=(ready,), daemon=True, name="friday-floatorb")
        self.thread.start()
        ready.wait(5)
        return self.running and bool(self.hwnd)

    def stop(self, wait: float = 3.0) -> None:
        thread, hwnd = self.thread, self.hwnd
        if hwnd and thread is not None and thread.is_alive():
            _win32()["user32"].PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
            thread.join(wait)  # 等它真的关好,马上再开也不会撞在一起

    def _menu(self, hwnd) -> int:
        """右键:弹出小选单,选了才做(不会一按右键就不见)。"""
        win = _win32()
        user32, wintypes, ctypes = win["user32"], win["wintypes"], win["ctypes"]
        menu = user32.CreatePopupMenu()
        user32.AppendMenuW(menu, 0, MENU_TALK, "说话")
        user32.AppendMenuW(menu, 0, MENU_OPEN, "打开星期五窗口")
        user32.AppendMenuW(menu, 0x800, 0, None)  # 分隔线
        user32.AppendMenuW(menu, 0, MENU_HIDE, "收起小光球")
        point = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(point))
        user32.SetForegroundWindow(hwnd)  # 不先这样做,选单点别处不会自己关
        choice = user32.TrackPopupMenu(menu, 0x0100 | 0x0002, point.x, point.y, 0, hwnd, None)  # 回传选了哪一项
        user32.PostMessageW(hwnd, 0, 0, 0)
        user32.DestroyMenu(menu)
        return choice

    # ---- 窗口线程 ----
    def _run(self, ready: threading.Event) -> None:
        try:
            win = _win32()
        except Exception:
            ready.set()
            return
        ctypes, wintypes, user32, gdi32 = win["ctypes"], win["wintypes"], win["user32"], win["gdi32"]
        HWND_TOPMOST = wintypes.HWND(-1)
        on_screen = self.x is not None and user32.MonitorFromPoint(
            wintypes.POINT(int(self.x) + SIZE // 2, int(self.y) + SIZE // 2), 0)  # MONITOR_DEFAULTTONULL
        if not on_screen:  # 第一次,或记住的位置不在任何屏幕上(换了屏幕):放在右下角、工作列上面一点
            self.x = user32.GetSystemMetrics(0) - SIZE - 40
            self.y = user32.GetSystemMetrics(1) - SIZE - 120

        screen_dc = user32.GetDC(None)
        mem_dc = gdi32.CreateCompatibleDC(screen_dc)
        info = win["BITMAPINFOHEADER"](ctypes.sizeof(win["BITMAPINFOHEADER"]), SIZE, -SIZE, 1, 32, 0, 0, 0, 0, 0, 0)
        bits = ctypes.c_void_p()
        bitmap = gdi32.CreateDIBSection(mem_dc, ctypes.byref(info), 0, ctypes.byref(bits), None, 0)
        old_bitmap = gdi32.SelectObject(mem_dc, bitmap)
        blend = win["BLENDFUNCTION"](0, 0, 255, 1)  # AC_SRC_OVER, 用每个点自己的透明度
        size = wintypes.SIZE(SIZE, SIZE)
        source = wintypes.POINT(0, 0)

        def draw(hwnd) -> None:
            try:
                color = parse_color(self.color())
                mode = self.mode() or "idle"
            except Exception:
                color, mode = parse_color(""), "idle"
            try:
                level = self.level()
            except Exception:
                level = None
            data = self.frames.get(color, mode, self.index, level)
            ctypes.memmove(bits, data, len(data))
            where = wintypes.POINT(int(self.x), int(self.y))
            user32.UpdateLayeredWindow(hwnd, screen_dc, ctypes.byref(where), ctypes.byref(size), mem_dc,
                                       ctypes.byref(source), 0, ctypes.byref(blend), 2)  # ULW_ALPHA

        def cursor() -> tuple[int, int]:
            point = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(point))
            return point.x, point.y

        def keep_visible(hwnd) -> None:
            """被“显示桌面”收起来、被别的置顶程序盖住:自己出来,回到最上面。"""
            if user32.IsIconic(hwnd) or not user32.IsWindowVisible(hwnd):
                user32.ShowWindow(hwnd, 4)  # SW_SHOWNOACTIVATE
            user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)  # 不动大小、位置、焦点

        def background(job) -> None:
            threading.Thread(target=lambda: _quiet(job), daemon=True).start()

        def handle(hwnd, msg, wparam, lparam):
            if msg == 0x0113:  # WM_TIMER:下一帧
                self.index += 1
                draw(hwnd)
                if self.index % CHECK_EVERY == 0:
                    keep_visible(hwnd)
                return 0
            if msg == 0x0112 and (wparam & 0xFFF0) == 0xF020:  # 有人叫它最小化(显示桌面):不理
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
                    background(self.on_click)
                self.dragging = False
                return 0
            if msg == 0x0205:  # 右键放开:小选单
                choice = self._menu(hwnd)
                if choice == MENU_TALK:
                    background(self.on_click)
                elif choice == MENU_OPEN:
                    background(self.on_open)
                elif choice == MENU_HIDE:
                    self.closed_by_menu = True
                    user32.PostMessageW(hwnd, 0x0010, 0, 0)
                return 0
            if msg == 0x0010:  # WM_CLOSE
                user32.KillTimer(hwnd, 1)
                user32.DestroyWindow(hwnd)
                return 0
            if msg == 0x0002:  # WM_DESTROY
                user32.PostQuitMessage(0)
                return 0
            return None  # 其他的交给 Windows

        hwnd = user32.CreateWindowExW(0x00080000 | 0x00000008 | 0x00000080 | 0x08000000, CLASS_NAME, "星期五",
                                      0x80000000, int(self.x), int(self.y), SIZE, SIZE, None, None, win["instance"], None)
        # WS_EX_LAYERED | WS_EX_TOPMOST | WS_EX_TOOLWINDOW(不出现在工作列)| WS_EX_NOACTIVATE(点它不会抢走焦点)
        if not hwnd:
            gdi32.SelectObject(mem_dc, old_bitmap)
            gdi32.DeleteObject(bitmap)
            gdi32.DeleteDC(mem_dc)
            user32.ReleaseDC(None, screen_dc)
            ready.set()
            return
        _HANDLERS[int(hwnd)] = handle
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
            _HANDLERS.pop(int(hwnd), None)
            gdi32.SelectObject(mem_dc, old_bitmap)
            gdi32.DeleteObject(bitmap)
            gdi32.DeleteDC(mem_dc)
            user32.ReleaseDC(None, screen_dc)
            self.hwnd = None
            if self.closed_by_menu:
                _quiet(self.on_close)


def _quiet(job) -> None:
    try:
        job()
    except Exception:
        pass
