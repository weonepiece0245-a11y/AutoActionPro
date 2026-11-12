"""打游戏时按住说话:按住一个键对星期五说话,放开就问;她直接用声音回答,不跳出窗口、不挡游戏画面。

在 齿轮设定 → 游戏 选键(F7/F8/F9、鼠标侧键、` 键),默认关。
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


def key_setting() -> str:
    name = os.environ.get("FRIDAY_PTT_KEY", "off").strip().lower()
    return name if name in KEYS else "off"


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
        self.key = key if key in KEYS else "off"
        if self.key == "off":
            return "按住说话关掉了。"
        if os.name != "nt" and self.key_down is key_down_windows:
            return "按住说话只能在 Windows 上用。"
        self.running = True
        self.thread = threading.Thread(target=self._loop, daemon=True, name="friday-ptt")
        self.thread.start()
        return f"打游戏时按住 {KEYS[self.key][0]} 跟我说话,放开就问。"

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
        vk = KEYS[self.key][1]
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
