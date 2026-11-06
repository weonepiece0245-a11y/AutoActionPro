"""语音对话:用麦克风说话,助手把回答读出来。

依赖(见 requirements-voice.txt):
    SpeechRecognition + PyAudio  录音、自动判断一句话说完
    pyttsx3                      朗读,使用系统自带的语音(离线)
    vosk                         可选的离线语音识别

语音识别有两种引擎:
    google  在线识别,免费、无需 Key,准确度好,但需要能访问 Google
    vosk    离线识别,首次使用会自动下载约 42MB 的中文模型
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
import zipfile
from typing import Callable

StatusFn = Callable[[str], None]

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
VOSK_MODEL_NAME = "vosk-model-small-cn-0.22"
VOSK_MODEL_URL = f"https://alphacephei.com/vosk/models/{VOSK_MODEL_NAME}.zip"
DEFAULT_MODEL_DIR = os.path.join(SCRIPT_DIR, "models")

LISTEN_TIMEOUT = 8  # 秒:这么久没开口就算没说话
PHRASE_LIMIT = 60  # 秒:一句话最长录多久

INSTALL_HINT = "语音功能需要额外安装依赖:python -m pip install -r assistant/requirements-voice.txt"

# Windows / macOS 上常见的中文语音名称
CHINESE_VOICE_HINTS = ("ZH-CN", "ZH_CN", "CHINESE", "HUIHUI", "YAOYAO", "KANGKANG", "XIAOXIAO", "TING-TING", "TINGTING")


class VoiceUnavailable(Exception):
    """缺少依赖或设备,无法使用语音。"""


def clean_for_speech(text: str) -> str:
    """去掉 Markdown 符号、代码和网址,只留下适合朗读的文字。"""
    text = re.sub(r"```.*?```", "(代码略)", text, flags=re.S)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)  # [文字](链接) -> 文字
    text = re.sub(r"https?://\S+", "(链接)", text)
    text = re.sub(r"^\s*\|?\s*:?-{3,}.*$", "", text, flags=re.M)  # 表格分隔行
    text = re.sub(r"^\s{0,3}(#{1,6}|>|[-*+]|\d+[.)])\s+", "", text, flags=re.M)  # 标题、引用、列表符号
    text = text.replace("|", ",")
    text = re.sub(r"[*_~#]+", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def _pick_chinese_voice(voices) -> str | None:
    for voice in voices:
        langs = " ".join(
            lang.decode(errors="ignore") if isinstance(lang, bytes) else str(lang)
            for lang in (getattr(voice, "languages", None) or [])
        )
        label = f"{voice.id} {voice.name} {langs}".upper()
        if any(hint in label for hint in CHINESE_VOICE_HINTS):
            return voice.id
    return None


class Speaker:
    """用系统自带的语音朗读文字(pyttsx3,离线)。"""

    def __init__(self, rate: int = 190) -> None:
        try:
            import pyttsx3
        except ImportError as e:
            raise VoiceUnavailable(INSTALL_HINT) from e
        self.engine = pyttsx3.init()
        self.engine.setProperty("rate", rate)
        voice_id = _pick_chinese_voice(self.engine.getProperty("voices"))
        self.has_chinese_voice = voice_id is not None
        if voice_id:
            self.engine.setProperty("voice", voice_id)

    def speak(self, text: str) -> None:
        text = clean_for_speech(text)
        if not text:
            return
        try:
            self.engine.say(text)
            self.engine.runAndWait()
        except KeyboardInterrupt:
            self.engine.stop()
            raise


def ensure_vosk_model(model_dir: str = DEFAULT_MODEL_DIR, on_status: StatusFn = print) -> str:
    """返回 vosk 中文模型的目录,没有的话先下载解压。"""
    path = os.path.join(model_dir, VOSK_MODEL_NAME)
    if os.path.isdir(path):
        return path
    os.makedirs(model_dir, exist_ok=True)
    archive = path + ".zip"
    on_status(f"第一次使用离线识别,正在下载中文语音模型(约 42MB):{VOSK_MODEL_URL}")
    tmp = archive + ".part"
    urllib.request.urlretrieve(VOSK_MODEL_URL, tmp)
    os.replace(tmp, archive)
    with zipfile.ZipFile(archive) as z:
        z.extractall(model_dir)
    os.remove(archive)
    on_status("模型下载完成。")
    return path


class Listener:
    """从麦克风录一句话并转成文字。"""

    def __init__(self, engine: str = "google", on_status: StatusFn = print) -> None:
        try:
            import speech_recognition as sr
        except ImportError as e:
            raise VoiceUnavailable(INSTALL_HINT) from e
        self.sr = sr
        self.engine = engine
        self.recognizer = sr.Recognizer()
        self.recognizer.pause_threshold = 1.0  # 停顿 1 秒算一句话说完
        try:
            self.microphone = sr.Microphone(sample_rate=16000)
            with self.microphone as source:
                on_status("正在适应环境噪音,请保持安静 1 秒……")
                self.recognizer.adjust_for_ambient_noise(source, duration=1)
        except (OSError, AttributeError) as e:
            # 没装 PyAudio 时 SpeechRecognition 抛 AttributeError,没有麦克风时抛 OSError
            raise VoiceUnavailable(f"无法打开麦克风:{e}。请检查麦克风是否连接,或{INSTALL_HINT}") from e

        self._vosk = None
        if engine == "vosk":
            try:
                import vosk
            except ImportError as e:
                raise VoiceUnavailable(INSTALL_HINT) from e
            vosk.SetLogLevel(-1)
            self._vosk = vosk.Model(ensure_vosk_model(on_status=on_status))

    def listen(self, on_status: StatusFn = print) -> str | None:
        """录一句话,返回识别出的文字;没听到或没听清时返回 None。"""
        sr = self.sr
        with self.microphone as source:
            on_status("我在听,请说话……")
            try:
                audio = self.recognizer.listen(source, timeout=LISTEN_TIMEOUT, phrase_time_limit=PHRASE_LIMIT)
            except sr.WaitTimeoutError:
                return None
        on_status("识别中……")
        if self._vosk is not None:
            return self._recognize_vosk(audio)
        try:
            return self.recognizer.recognize_google(audio, language="zh-CN") or None
        except sr.UnknownValueError:
            return None
        except sr.RequestError as e:
            raise VoiceUnavailable(
                f"在线语音识别连接失败({e})。如果访问不了 Google,请改用离线识别:加参数 --stt vosk"
            ) from e

    def _recognize_vosk(self, audio) -> str | None:
        import vosk

        recognizer = vosk.KaldiRecognizer(self._vosk, 16000)
        recognizer.AcceptWaveform(audio.get_raw_data(convert_rate=16000, convert_width=2))
        text = json.loads(recognizer.FinalResult()).get("text", "")
        text = text.replace(" ", "")  # vosk 的中文结果字词之间带空格
        return text or None


class VoiceIO:
    """把听和说组合在一起,供聊天循环使用。"""

    def __init__(self, listener, speaker) -> None:
        self.listener = listener
        self.speaker = speaker

    def listen(self, on_status: StatusFn) -> str | None:
        return self.listener.listen(on_status)

    def speak(self, text: str) -> None:
        self.speaker.speak(text)


def create_voice(engine: str = "google", on_status: StatusFn = print) -> VoiceIO:
    speaker = Speaker()
    if not speaker.has_chinese_voice:
        on_status(
            "没有找到中文朗读语音,可能读不出中文。Windows 可在“设置 → 时间和语言 → 语音”里添加中文语音。"
        )
    return VoiceIO(Listener(engine, on_status), speaker)
