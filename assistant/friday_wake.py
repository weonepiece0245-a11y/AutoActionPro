"""喊“星期五”就回应:在电脑本地一直听唤醒词(离线的 Vosk,不上传声音、不花钱)。

听到“星期五”后,把窗口叫到前面并开始录音,接下来说的话才会像平常一样交给 Claude。
星期五自己在说话、或者窗口正在录音时暂停,免得听到自己的声音又被叫醒。
第一次打开会自动安装 vosk、sounddevice,并下载约 42MB 的中文语音模型。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable

WAKE_WORD = "星期五"
# 可选的唤醒词:越长越不容易被电视、视频里的话误叫醒(识别器要的是空格分开的词)
# 每个唤醒词给几种写法:模型词表里没有某个词时(例如“嘿”),还能用拆开的字或同音字认出来
PHRASES = {
    "你好星期五": ["你好 星期五", "你 好 星期五"],
    "嘿星期五": ["嘿 星期五", "黑 星期五", "嗨 星期五"],  # 英文说“hey 星期五”也会听成这几种
    "星期五": ["星期五"],
}
DEFAULT_PHRASE = "你好星期五"
# 只让识别器在这些词里选,别的声音都归到 [unk];放进其他“星期几”是为了不把它们听成“星期五”
DISTRACTORS = ["星期五", "星期一", "星期二", "星期三", "星期四", "星期六", "星期天", "星期日", "你好", "[unk]"]
SENSITIVITY = {"high": 0.6, "normal": 0.75, "low": 0.9}  # 灵敏度高 = 把握度要求低,容易叫醒也容易误判
MIN_CONFIDENCE = SENSITIVITY["normal"]


CUSTOM_MIN, CUSTOM_MAX = 2, 10
KEEP_WORDS = ["星期五", "你好"]  # 自定义唤醒词里有这些就当一个词,其余拆成单字


def check_custom(phrase: str) -> str:
    """检查自定义唤醒词;不行就抛 ValueError(说明原因)。返回整理后的唤醒词。"""
    phrase = "".join(phrase.split())
    if not CUSTOM_MIN <= len(phrase) <= CUSTOM_MAX:
        raise ValueError(f"唤醒词要 {CUSTOM_MIN}~{CUSTOM_MAX} 个字(建议 3~6 个字,太短容易误判)")
    if not all("\u4e00" <= c <= "\u9fff" for c in phrase):
        raise ValueError("唤醒词只能用中文字(离线识别听不懂英文和符号)")
    return phrase


def spellings(phrase: str) -> list[str]:
    """给识别器的几种写法:整个词、拆成单字、保留常见词再拆(模型词表里没有整个词时也能认出来)。"""
    if phrase in PHRASES:
        return PHRASES[phrase]
    forms = [phrase, " ".join(phrase)]
    kept = phrase
    for word in KEEP_WORDS:
        kept = kept.replace(word, f"\0{word}\0")
    parts = []
    for chunk in kept.split("\0"):
        parts.extend([chunk] if chunk in KEEP_WORDS else list(chunk))
    forms.append(" ".join(p for p in parts if p))
    return list(dict.fromkeys(forms))


def wake_phrase() -> str:
    value = os.environ.get("FRIDAY_WAKE_PHRASE", "").strip().replace(" ", "")
    if value in PHRASES:
        return value
    try:
        return check_custom(value)
    except ValueError:
        return DEFAULT_PHRASE


def wake_sensitivity() -> str:
    value = os.environ.get("FRIDAY_WAKE_SENSITIVITY", "").strip().lower()
    return value if value in SENSITIVITY else "normal"


def grammar(phrase: str) -> list[str]:
    forms = spellings(phrase)
    return forms + [w for w in DISTRACTORS if w not in forms]


def _forms(phrase: str) -> list[str]:
    """识别结果去掉空格后,算作唤醒词的几种文字。"""
    return [s.replace(" ", "") for s in PHRASES.get(phrase, [phrase])] if phrase in PHRASES else [phrase]
COOLDOWN = 3.0  # 秒:叫醒一次后这么久内不再触发
RESUME_DELAY = 1.0  # 秒:星期五说完、录音结束后,再等这么久才重新开始听
SAMPLE_RATE = 16000
BLOCK = 4000  # 每次读 0.25 秒


def heard_wake_word(result_json: str, min_confidence: float = MIN_CONFIDENCE, phrase: str = WAKE_WORD) -> bool:
    """Vosk 的一句识别结果里有没有整句唤醒词(有逐字把握度时,唤醒词里每个词都要够高)。"""
    try:
        result = json.loads(result_json)
    except ValueError:
        return False
    text = str(result.get("text", "")).replace(" ", "")
    form = next((f for f in _forms(phrase) if f in text), None)
    if form is None:
        return False
    words = result.get("result")
    if not words:
        return True
    confidences = [w.get("conf", 1.0) for w in words if str(w.get("word", "")) and str(w.get("word", "")) in form]
    return bool(confidences) and min(confidences) >= min_confidence


def pip_install(packages: list[str], run=subprocess.run) -> bool:
    python = Path(sys.executable)
    if python.name.lower() == "pythonw.exe" and python.with_name("python.exe").exists():
        python = python.with_name("python.exe")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = run([str(python), "-m", "pip", "install", "-q", "--disable-pip-version-check", *packages],
               capture_output=True, timeout=900, creationflags=flags)
    return proc.returncode == 0


class WakeWord:
    def __init__(self, on_wake: Callable[[], None], on_status: Callable[[str, bool], None],
                 paused: Callable[[], bool], clock: Callable[[], float] = time.monotonic) -> None:
        self.on_wake = on_wake
        self.on_status = on_status  # (文字, 是否出错)
        self.paused = paused
        self.clock = clock
        self.state = "off"  # off / preparing / listening / error
        self.error = ""
        self.running = False
        self.thread: threading.Thread | None = None
        self.last_wake = -1e9
        self.paused_until = 0.0
        self.phrase = wake_phrase()

    # ---------- 判断 ----------
    def should_wake(self, result_json: str) -> bool:
        now = self.clock()
        if self.paused():
            self.paused_until = now + RESUME_DELAY
            return False
        if now < self.paused_until or now - self.last_wake < COOLDOWN:
            return False
        if heard_wake_word(result_json, SENSITIVITY[wake_sensitivity()], self.phrase):
            self.last_wake = now
            return True
        return False

    # ---------- 开关 ----------
    def start(self) -> None:
        if self.running:
            return
        self.running = True
        self.state, self.error = "preparing", ""
        self.thread = threading.Thread(target=self._run, daemon=True, name="friday-wake")
        self.thread.start()

    def stop(self) -> None:
        self.running = False
        self.state = "off"

    def restart(self) -> None:
        """换了唤醒词:重新开始听。"""
        was_running = self.running
        self.stop()
        if self.thread is not None:
            self.thread.join(2)
        self.phrase = wake_phrase()
        if was_running:
            self.start()

    def _prepare(self):
        try:
            import sounddevice
            import vosk
        except ImportError:
            self.on_status("第一次使用唤醒功能,正在安装语音组件(只需要一次)……", False)
            if not pip_install(["vosk", "sounddevice"]):
                raise RuntimeError("语音组件安装失败,检查一下网络再打开一次")
            import sounddevice
            import vosk
        import voice

        model_dir = voice.ensure_vosk_model(on_status=lambda text: self.on_status(text, False))
        vosk.SetLogLevel(-1)
        return vosk, sounddevice, vosk.Model(model_dir)

    def _run(self) -> None:
        try:
            vosk, sd, model = self._prepare()
            self.phrase = wake_phrase()
            recognizer = vosk.KaldiRecognizer(model, SAMPLE_RATE, json.dumps(grammar(self.phrase), ensure_ascii=False))
            recognizer.SetWords(True)
            with sd.RawInputStream(samplerate=SAMPLE_RATE, blocksize=BLOCK, dtype="int16", channels=1) as stream:
                self.state = "listening"
                self.on_status(f"唤醒已开启:说“{self.phrase}”就能叫我", False)
                while self.running:
                    data, _ = stream.read(BLOCK)
                    if self.paused():
                        self.paused_until = self.clock() + RESUME_DELAY
                        recognizer.Reset()
                        continue
                    if recognizer.AcceptWaveform(bytes(data)) and self.should_wake(recognizer.Result()):
                        recognizer.Reset()
                        try:
                            self.on_wake()
                        except Exception:
                            pass
        except Exception as e:
            self.state, self.error = "error", str(e) or type(e).__name__
            self.on_status(f"唤醒功能没能开启:{self.error}", True)
        finally:
            self.running = False
            if self.state != "error":
                self.state = "off"
