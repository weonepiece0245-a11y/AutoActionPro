"""语音对话:用麦克风说话,助手把回答读出来。

依赖(见 requirements-voice.txt):
    SpeechRecognition + sounddevice  录音、自动判断一句话说完
                                 (sounddevice 自带录音组件,任何 Python 版本都不用编译)
    pywin32 (Windows)            朗读:直接调用 Windows 自带的语音引擎 SAPI(离线)
    pyttsx3 (macOS / Linux)      朗读:系统自带的语音(离线)
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
CHINESE_VOICE_HINTS = (
    "ZH-CN", "ZH_CN", "ZH-TW", "ZH-HK", "CHINESE", "中文",
    "HUIHUI", "YAOYAO", "KANGKANG", "XIAOXIAO", "HANHAN", "TRACY", "TING-TING", "TINGTING",
)
NO_CHINESE_VOICE_HINT = (
    "没有找到中文朗读语音,读中文时会没有声音。"
    "请在 Windows“设置 → 时间和语言 → 语音 → 管理语音”里添加“中文(简体)”,然后重新打开。"
)


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


def _is_chinese(label: str) -> bool:
    label = label.upper()
    return any(hint.upper() in label for hint in CHINESE_VOICE_HINTS)


def _pick_chinese_voice(voices) -> str | None:
    for voice in voices:
        langs = " ".join(
            lang.decode(errors="ignore") if isinstance(lang, bytes) else str(lang)
            for lang in (getattr(voice, "languages", None) or [])
        )
        if _is_chinese(f"{voice.id} {voice.name} {langs}"):
            return voice.id
    return None


class WindowsSpeaker:
    """直接调用 Windows 自带的语音引擎 SAPI 朗读(离线)。

    比 pyttsx3 更可靠:pyttsx3 在部分 Windows / Python 版本上会不出声也不报错。
    """

    SVSF_ASYNC = 1
    SVSF_PURGE = 2

    def __init__(self, rate: int = 1) -> None:
        try:
            import win32com.client
        except ImportError as e:
            raise VoiceUnavailable(INSTALL_HINT) from e
        try:
            self.sapi = win32com.client.Dispatch("SAPI.SpVoice")
        except Exception as e:
            raise VoiceUnavailable(f"无法启动 Windows 语音引擎:{e}") from e
        self.sapi.Rate = rate  # -10(最慢)到 10(最快)

        tokens = self.sapi.GetVoices()
        voices = [tokens.Item(i) for i in range(tokens.Count)]
        self.voice_names = [v.GetDescription() for v in voices]
        chinese = [v for v, name in zip(voices, self.voice_names) if _is_chinese(f"{name} {v.Id}")]
        self.has_chinese_voice = bool(chinese)
        if chinese:
            self.sapi.Voice = chinese[0]
            self.voice_name = chinese[0].GetDescription()
        else:
            self.voice_name = self.voice_names[0] if self.voice_names else "(无)"

    def speak(self, text: str) -> None:
        text = clean_for_speech(text)
        if not text:
            return
        # 异步朗读并分段等待,这样朗读时按 Ctrl+C 能立刻停下
        self.sapi.Speak(text, self.SVSF_ASYNC | self.SVSF_PURGE)
        try:
            while not self.sapi.WaitUntilDone(100):
                pass
        except KeyboardInterrupt:
            self.sapi.Speak("", self.SVSF_ASYNC | self.SVSF_PURGE)
            raise


class Speaker:
    """用系统自带的语音朗读文字(pyttsx3,离线;用于 macOS / Linux)。"""

    def __init__(self, rate: int = 190) -> None:
        try:
            import pyttsx3
        except ImportError as e:
            raise VoiceUnavailable(INSTALL_HINT) from e
        self.engine = pyttsx3.init()
        self.engine.setProperty("rate", rate)
        voices = self.engine.getProperty("voices")
        self.voice_names = [v.name for v in voices]
        voice_id = _pick_chinese_voice(voices)
        self.has_chinese_voice = voice_id is not None
        if voice_id:
            self.engine.setProperty("voice", voice_id)
        self.voice_name = next((v.name for v in voices if v.id == voice_id), "系统默认")

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


def make_microphone(sr, sample_rate: int = 16000, chunk_size: int = 1024):
    """用 sounddevice 录音的麦克风,可以直接交给 SpeechRecognition 使用。

    SpeechRecognition 自带的 Microphone 依赖 PyAudio,而 PyAudio 在较新的 Python 上
    没有现成的安装包,需要编译。这里实现同样的接口:进入 with 时打开录音流,
    stream.read(n) 返回 n 帧 16 位单声道音频。
    """
    import sounddevice as sd

    class _Reader:
        def __init__(self, raw_stream) -> None:
            self.raw_stream = raw_stream

        def read(self, frames: int) -> bytes:
            data, _overflowed = self.raw_stream.read(frames)
            return bytes(data)

    class SoundDeviceMicrophone(sr.AudioSource):
        def __init__(self) -> None:
            self.SAMPLE_RATE = sample_rate
            self.SAMPLE_WIDTH = 2  # 16 位
            self.CHUNK = chunk_size
            self.stream = None
            self._raw = None

        def __enter__(self):
            self._raw = sd.RawInputStream(
                samplerate=self.SAMPLE_RATE, channels=1, dtype="int16", blocksize=self.CHUNK
            )
            self._raw.start()
            self.stream = _Reader(self._raw)
            return self

        def __exit__(self, *exc):
            try:
                self._raw.stop()
                self._raw.close()
            finally:
                self._raw = None
                self.stream = None
            return False

    return SoundDeviceMicrophone()


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
            self.microphone = make_microphone(sr)
        except ImportError as e:
            raise VoiceUnavailable(INSTALL_HINT) from e
        try:
            with self.microphone as source:
                on_status("正在适应环境噪音,请保持安静 1 秒……")
                self.recognizer.adjust_for_ambient_noise(source, duration=1)
        except Exception as e:  # 没有麦克风、麦克风被占用或没有权限
            raise VoiceUnavailable(
                f"无法打开麦克风:{e}。请检查麦克风是否插好,"
                "以及 Windows“设置 → 隐私和安全性 → 麦克风”里是否允许桌面应用使用麦克风"
            ) from e

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


def make_speaker():
    return WindowsSpeaker() if os.name == "nt" else Speaker()


def create_voice(engine: str = "google", on_status: StatusFn = print) -> VoiceIO:
    speaker = make_speaker()
    if not speaker.has_chinese_voice:
        on_status(NO_CHINESE_VOICE_HINT)
    return VoiceIO(Listener(engine, on_status), speaker)


def self_test() -> int:
    """检查朗读和麦克风:列出系统语音、读一句测试语,再录一句话。"""
    print("== 1. 朗读测试 ==")
    try:
        speaker = make_speaker()
    except VoiceUnavailable as e:
        print(f"朗读不可用:{e}")
        return 1
    print("系统里的朗读语音:")
    for name in speaker.voice_names or ["(一个都没有)"]:
        print(f"  - {name}")
    print(f"正在使用:{speaker.voice_name}")
    if not speaker.has_chinese_voice:
        print(NO_CHINESE_VOICE_HINT)
    print("现在会读一句:“你好,我是星期五,能听到我说话吗?”")
    speaker.speak("你好,我是星期五,能听到我说话吗?")
    print("读完了。如果没听到声音,请检查音量和默认的扬声器/耳机。")

    print("\n== 2. 麦克风测试 ==")
    try:
        listener = Listener("google", print)
        text = listener.listen(print)
    except VoiceUnavailable as e:
        print(f"麦克风或识别不可用:{e}")
        return 1
    print(f"识别结果:{text or '(没听到或没听清)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(self_test())
