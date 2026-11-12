"""打游戏时按住说话:按住一个键对星期五说话,放开就问;她直接用声音回答,不跳出窗口、不挡游戏画面。

在 齿轮设定 → 游戏 选键(F7/F8/F9、鼠标侧键、` 键),或选“自定义”再按一下你想用的键(键盘上的键、鼠标中键或侧键),默认关。
只是“看”那个键有没有按着(和游戏读键盘一样),不拦截、不改按键,游戏照样收到这个键,所以选一个游戏里没用到的。
按下时“嘀”一声开始录音,放开“嘟”一声就送出去;按不到半秒当成按错,不问。最长录 30 秒。
第一次用会自动安装录音组件 sounddevice。
"""

from __future__ import annotations

import io
import os
import threading
import time
import wave
from typing import Callable

KEYS = {  # 名字: (给你看的, Windows 键码)
    "f7": ("F7", 0x76), "f8": ("F8", 0x77), "f9": ("F9", 0x78),
    "mouse4": ("鼠标侧键(后)", 0x05), "mouse5": ("鼠标侧键(前)", 0x06), "backquote": ("` 键(Esc 下面)", 0xC0),
}
RATE = 16000
CHUNK = 800  # 0.05 秒
MIN_SECONDS = 0.5
MAX_SECONDS = 30
POLL = 0.025


NOT_ALLOWED = {0x01: "鼠标左键", 0x02: "鼠标右键"}  # 每次点东西都会触发,不能用


def custom_key(name: str) -> int | None:
    """“vk:119” -> 119;不是自定义的键、或不能用的键返回 None。"""
    if not str(name).startswith("vk:"):
        return None
    try:
        vk = int(str(name)[3:])
    except ValueError:
        return None
    return vk if 3 <= vk <= 254 and vk not in NOT_ALLOWED else None


# 用到按键的功能:(存哪个键的设定, 存自定义键名字的设定)
SETTINGS = {"ptt": ("FRIDAY_PTT_KEY", "FRIDAY_PTT_LABEL"), "shot": ("FRIDAY_SHOT_KEY", "FRIDAY_SHOT_LABEL")}


def key_setting(which: str = "ptt") -> str:
    name = os.environ.get(SETTINGS[which][0], "off").strip().lower()
    return name if name in KEYS or custom_key(name) is not None else "off"


def clean_label(label: str) -> str:
    label = " ".join(str(label or "").split())[:20]
    return label or "自定义的键"


def key_info(name: str, which: str = "ptt") -> tuple[str, int] | None:
    """(给你看的名字, Windows 键码);关着或不认识返回 None。"""
    if name in KEYS:
        return KEYS[name]
    vk = custom_key(name)
    if vk is None:
        return None
    return clean_label(os.environ.get(SETTINGS[which][1], "")), vk


def to_wav(pcm: bytes, rate: int = RATE) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return out.getvalue()


def key_down_windows(vk: int) -> bool:
    import ctypes

    return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)


def beep(high: bool) -> None:
    if os.name != "nt":
        return
    try:
        import winsound

        threading.Thread(target=lambda: winsound.Beep(1200 if high else 700, 70), daemon=True).start()
    except Exception:
        pass


class SoundDeviceRecorder:
    """用 sounddevice 录 16kHz 单声道。"""

    def __init__(self) -> None:
        import sounddevice

        self.sd = sounddevice
        self.stream = None
        self.chunks: list[bytes] = []

    def start(self) -> None:
        self.chunks = []

        def keep(data, frames, time_info, status) -> None:
            self.chunks.append(bytes(data))
        self.stream = self.sd.RawInputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=CHUNK, callback=keep)
        self.stream.start()

    def stop(self) -> bytes:
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            finally:
                self.stream = None
        return b"".join(self.chunks)


class PushToTalk:
    def __init__(self, on_audio: Callable[[bytes], None], on_status: Callable[[str, bool], None] = lambda t, e: None,
                 key_down: Callable[[int], bool] | None = None, recorder_factory: Callable[[], object] | None = None,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
                 beep: Callable[[bool], None] = beep, paused: Callable[[], bool] = lambda: False) -> None:
        self.on_audio = on_audio  # 放开后:一段 WAV
        self.on_status = on_status
        self.key_down = key_down or key_down_windows
        self.recorder_factory = recorder_factory or SoundDeviceRecorder
        self.clock, self.sleep, self.beep = clock, sleep, beep
        self.paused = paused  # 锁着的时候不听
        self.key = "off"
        self.running = False
        self.thread: threading.Thread | None = None
        self.recorder = None

    def start(self, key: str) -> str:
        """开始看这个键;返回给你看的一句话。"""
        self.stop()
        info = key_info(key)
        self.key = key if info else "off"
        if self.key == "off":
            return "按住说话关掉了。"
        self.vk = info[1]
        if os.name != "nt" and self.key_down is key_down_windows:
            return "按住说话只能在 Windows 上用。"
        self.running = True
        self.thread = threading.Thread(target=self._loop, daemon=True, name="friday-ptt")
        self.thread.start()
        return f"打游戏时按住 {info[0]} 跟我说话,放开就问。"

    def stop(self) -> None:
        self.running = False
        if self.thread is not None and self.thread is not threading.current_thread():
            self.thread.join(1)
        self.thread = None

    def _recorder(self):
        if self.recorder is None:
            try:
                self.recorder = self.recorder_factory()
            except ImportError:
                self.on_status("第一次用按住说话:在安装录音组件(一两分钟)……", False)
                import friday_wake

                if not friday_wake.pip_install(["sounddevice"]):
                    raise RuntimeError("录音组件没装好,检查一下网络") from None
                self.recorder = self.recorder_factory()
        return self.recorder

    def _loop(self) -> None:
        vk = self.vk
        was_down = False
        started = 0.0
        recording = False
        while self.running:
            try:
                down = self.key_down(vk)
            except Exception:
                down = False
            if down and not was_down and not self.paused():
                try:
                    self._recorder().start()
                    recording, started = True, self.clock()
                    self.beep(True)
                except Exception as e:  # noqa: BLE001
                    self.on_status(f"按住说话打不开麦克风:{e}", True)
                    recording = False
            elif recording and (not down or self.clock() - started >= MAX_SECONDS):
                recording = False
                try:
                    pcm = self.recorder.stop()
                except Exception:
                    pcm = b""
                self.beep(False)
                if self.clock() - started >= MIN_SECONDS and pcm:
                    try:
                        self.on_audio(to_wav(pcm))
                    except Exception as e:  # noqa: BLE001
                        self.on_status(f"按住说话没送出去:{type(e).__name__}", True)
                if down:  # 超过 30 秒:等放开再算下一次
                    while self.running and self.key_down(vk):
                        self.sleep(POLL)
                    down = False
            was_down = down
            self.sleep(POLL)
        if recording and self.recorder is not None:
            try:
                self.recorder.stop()
            except Exception:
                pass


class KeyTap:
    """按一下某个键就做一件事(截图问星期五用)。和按住说话一样只是看键有没有按下,不拦截。"""

    def __init__(self, on_press: Callable[[], None], key_down: Callable[[int], bool] | None = None,
                 sleep: Callable[[float], None] = time.sleep, paused: Callable[[], bool] = lambda: False,
                 which: str = "shot") -> None:
        self.on_press = on_press
        self.key_down = key_down or key_down_windows
        self.sleep = sleep
        self.paused = paused
        self.which = which
        self.key = "off"
        self.vk = 0
        self.running = False
        self.thread: threading.Thread | None = None

    def start(self, key: str, what: str = "截图问星期五") -> str:
        self.stop()
        info = key_info(key, self.which)
        self.key = key if info else "off"
        if self.key == "off":
            return f"{what}关掉了。"
        if os.name != "nt" and self.key_down is key_down_windows:
            return f"{what}只能在 Windows 上用。"
        self.vk = info[1]
        self.running = True
        self.thread = threading.Thread(target=self._loop, daemon=True, name=f"friday-key-{self.which}")
        self.thread.start()
        return f"按一下 {info[0]} 就{what}。"

    def stop(self) -> None:
        self.running = False
        if self.thread is not None and self.thread is not threading.current_thread():
            self.thread.join(1)
        self.thread = None

    def _loop(self) -> None:
        was_down = False
        while self.running:
            try:
                down = self.key_down(self.vk)
            except Exception:
                down = False
            if down and not was_down and not self.paused():
                try:
                    self.on_press()
                except Exception:
                    pass
            was_down = down
            self.sleep(POLL)
