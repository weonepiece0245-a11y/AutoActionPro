"""框选屏幕:把整个屏幕先“冻住”,你用鼠标拉一个框,框里的画面存成图片交给星期五。

    python friday_snip.py <存到哪个 png>

拉好放开就完成;按 Esc 或右键取消。多个屏幕也可以(整个桌面一起冻住)。
在自己的小程序里跑:就算卡住也不会拖住星期五。
"""

from __future__ import annotations

import os
import sys

MIN_SIZE = 8  # 太小的框当成点错


def virtual_screen() -> tuple[int, int, int, int]:
    """整个桌面(所有屏幕)的 左, 上, 宽, 高。"""
    if os.name == "nt":
        import ctypes

        user32 = ctypes.windll.user32
        return (user32.GetSystemMetrics(76), user32.GetSystemMetrics(77),
                user32.GetSystemMetrics(78), user32.GetSystemMetrics(79))
    return 0, 0, 0, 0


def crop_box(start: tuple[int, int], end: tuple[int, int], size: tuple[int, int]) -> tuple[int, int, int, int] | None:
    """拉的两个角 -> (左, 上, 右, 下);太小或在画面外返回 None。"""
    left, right = sorted((start[0], end[0]))
    top, bottom = sorted((start[1], end[1]))
    left, top = max(0, left), max(0, top)
    right, bottom = min(size[0], right), min(size[1], bottom)
    if right - left < MIN_SIZE or bottom - top < MIN_SIZE:
        return None
    return left, top, right, bottom


def run(out: str) -> int:
    if os.name == "nt":
        import ctypes

        try:  # 高分辨率屏幕:用真正的像素,框的位置才对得上
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass
    import tkinter as tk

    from PIL import Image, ImageEnhance, ImageGrab, ImageTk

    shot = ImageGrab.grab(all_screens=True)
    x0, y0, width, height = virtual_screen()
    if not width:
        width, height = shot.size
    dark = ImageEnhance.Brightness(shot).enhance(0.55)

    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.geometry(f"{width}x{height}+{x0}+{y0}")
    root.configure(cursor="crosshair")
    canvas = tk.Canvas(root, width=width, height=height, highlightthickness=0, bd=0)
    canvas.pack(fill="both", expand=True)
    background = ImageTk.PhotoImage(dark)
    canvas.create_image(0, 0, image=background, anchor="nw")
    canvas.create_text(width // 2, 40, text="拉一个框选要问的地方   ·   Esc 或右键取消", fill="#e6f6ff",
                       font=("Microsoft YaHei", 16, "bold"))
    state = {"start": None, "rect": None, "bright": None, "photo": None, "result": 1}

    def press(event) -> None:
        state["start"] = (event.x, event.y)

    def drag(event) -> None:
        if state["start"] is None:
            return
        box = crop_box(state["start"], (event.x, event.y), shot.size)
        for key in ("rect", "bright"):
            if state[key] is not None:
                canvas.delete(state[key])
                state[key] = None
        if box is None:
            return
        state["photo"] = ImageTk.PhotoImage(shot.crop(box))  # 框里的显示原来的亮度
        state["bright"] = canvas.create_image(box[0], box[1], image=state["photo"], anchor="nw")
        state["rect"] = canvas.create_rectangle(*box, outline="#22d3ee", width=2)

    def release(event) -> None:
        if state["start"] is None:
            return
        box = crop_box(state["start"], (event.x, event.y), shot.size)
        state["start"] = None
        if box is None:
            return
        image: Image.Image = shot.crop(box)
        image.save(out, "PNG")
        state["result"] = 0
        root.destroy()

    def cancel(_event=None) -> None:
        root.destroy()

    canvas.bind("<ButtonPress-1>", press)
    canvas.bind("<B1-Motion>", drag)
    canvas.bind("<ButtonRelease-1>", release)
    root.bind("<Escape>", cancel)
    canvas.bind("<ButtonPress-3>", cancel)
    root.after(60000, cancel)  # 一分钟没框就算了
    root.focus_force()
    root.mainloop()
    return state["result"]


if __name__ == "__main__":
    sys.exit(run(sys.argv[1]) if len(sys.argv) > 1 else 2)
