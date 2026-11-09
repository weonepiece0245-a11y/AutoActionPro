"""语音对话:用麦克风说话,助手把回答读出来。

依赖(见 requirements-voice.txt):
    sounddevice                  录音(自带录音组件,任何 Python 版本都不用编译)
    SpeechRecognition            在线语音识别
    edge-tts                     朗读(默认):微软 Edge“大声朗读”的神经网络语音,自然好听,需要联网
    pywin32 (Windows)            朗读:Windows 自带的语音引擎 SAPI(离线,也是联网失败时的备用)
    pyttsx3 (macOS / Linux)      朗读:系统自带的语音(离线)
    vosk                         可选的离线语音识别

语音识别有两种引擎:
    google  在线识别,免费、无需 Key,准确度好,但需要能访问 Google
    vosk    离线识别,首次使用会自动下载约 42MB 的中文模型
"""

from __future__ import annotations

import array
import asyncio
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.request
import zipfile
from typing import Callable

StatusFn = Callable[[str], None]

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
VOSK_MODEL_NAME = "vosk-model-small-cn-0.22"
VOSK_MODEL_URL = f"https://alphacephei.com/vosk/models/{VOSK_MODEL_NAME}.zip"
DEFAULT_MODEL_DIR = os.path.join(SCRIPT_DIR, "models")

LISTEN_TIMEOUT = 10  # 秒:这么久没开口就算没说话
PHRASE_LIMIT = 60  # 秒:一句话最长录多久

INSTALL_HINT = "语音功能需要额外安装依赖:python -m pip install -r assistant/requirements-voice.txt"

# Windows / macOS 上常见的中文语音名称
CHINESE_VOICE_HINTS = (
    "ZH-CN", "ZH_CN", "ZH-TW", "ZH-HK", "CHINESE", "中文",
    "HUIHUI", "YAOYAO", "KANGKANG", "XIAOXIAO", "HANHAN", "TRACY", "TING-TING", "TINGTING",
)
NO_CHINESE_VOICE_HINT = (
    "没有找到中文朗读语音,读中文时会没有声音。"
    "请在 Windows“设置 → 时间和语言 → 语音 → 管理语音 → 添加语音”里添加“中文(简体,中国)”,然后重新打开。"
)


# 微软 Edge 在线语音:简称 -> (语音名, 说明)
EDGE_VOICES = {
    "xiaoxiao": ("zh-CN-XiaoxiaoNeural", "晓晓,女声,温暖自然(默认)"),
    "xiaoyi": ("zh-CN-XiaoyiNeural", "晓伊,女声,活泼"),
    "yunxi": ("zh-CN-YunxiNeural", "云希,男声,阳光"),
    "yunjian": ("zh-CN-YunjianNeural", "云健,男声,沉稳"),
    "yunyang": ("zh-CN-YunyangNeural", "云扬,男声,新闻播报风格"),
    "hsiaochen": ("zh-TW-HsiaoChenNeural", "晓臻,台湾女声"),
}
DEFAULT_EDGE_VOICE = EDGE_VOICES["xiaoxiao"][0]


class VoiceUnavailable(Exception):
    """缺少依赖或设备,无法使用语音。"""


def resolve_edge_voice(name: str | None) -> str:
    """把 xiaoxiao 这样的简称换成完整语音名;完整名称原样返回。"""
    if not name:
        return DEFAULT_EDGE_VOICE
    entry = EDGE_VOICES.get(name.strip().lower())
    return entry[0] if entry else name.strip()


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
    # Windows 10/11 在“设置 → 语音”里添加的新式语音装在这里,SAPI 默认不列出来
    ONECORE_VOICES = r"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Speech_OneCore\Voices"

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

        voices = self._all_voices(win32com.client)
        self.voice_names = [name for name, _ in voices]
        self.voice_name = self.voice_names[0] if voices else "(无)"
        self.has_chinese_voice = False
        for name, token in voices:
            if not _is_chinese(f"{name} {token.Id}"):
                continue
            try:
                self.sapi.Voice = token
            except Exception:  # 个别新式语音不能直接用于 SAPI,换下一个
                continue
            self.voice_name = name
            self.has_chinese_voice = True
            break

    def _all_voices(self, client) -> list:
        """返回 [(名称, 语音)]:先列传统的桌面语音,再列新式语音,同名的只保留一个。"""
        groups = []
        tokens = self.sapi.GetVoices()
        groups.append([tokens.Item(i) for i in range(tokens.Count)])
        try:
            category = client.Dispatch("SAPI.SpObjectTokenCategory")
            category.SetId(self.ONECORE_VOICES, False)
            tokens = category.EnumerateTokens()
            groups.append([tokens.Item(i) for i in range(tokens.Count)])
        except Exception:  # 老版本 Windows 没有新式语音
            pass
        voices, seen = [], set()
        for token in (t for group in groups for t in group):
            name = token.GetDescription()
            if name not in seen:
                seen.add(name)
                voices.append((name, token))
        return voices

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


SAMPLE_RATE = 16000
CHUNK = 1024  # 每次读 1024 帧,约 0.064 秒
CHUNK_SECONDS = CHUNK / SAMPLE_RATE
DEFAULT_PAUSE = 2.0  # 秒:停顿这么久才算一句话说完
CALIBRATE_SECONDS = 1.0
PRE_ROLL_SECONDS = 0.4  # 开口前保留的一小段,避免吞掉第一个字
START_RATIO = 2.5  # 音量超过环境噪音的这么多倍才算开口
MIN_START_LEVEL = 120
KEEP_RATIO = 0.5  # 说话中途音量只要高于开口门槛的一半,就不算停顿


def rms(data: bytes) -> float:
    """16 位单声道音频的音量(均方根)。"""
    samples = array.array("h", data[: len(data) - len(data) % 2])
    if not samples:
        return 0.0
    return math.sqrt(sum(x * x for x in samples) / len(samples))


def enter_pressed() -> bool:
    """录音时用户有没有按回车(只在 Windows 上检测)。"""
    if os.name != "nt":
        return False
    import msvcrt

    pressed = False
    while msvcrt.kbhit():
        if msvcrt.getwch() in ("\r", "\n"):
            pressed = True
    return pressed


class Listener:
    """从麦克风录一句话并转成文字。

    自己判断一句话什么时候说完:开口后,连续停顿 pause 秒(默认 2 秒)才结束;
    说话中途声音变小不算停顿;Windows 上也可以按回车立刻结束。
    """

    def __init__(
        self,
        engine: str = "google",
        on_status: StatusFn = print,
        pause: float | None = None,
        key_pressed: Callable[[], bool] = enter_pressed,
    ) -> None:
        try:
            import sounddevice
            import speech_recognition as sr
        except ImportError as e:
            raise VoiceUnavailable(INSTALL_HINT) from e
        self.sr = sr
        self.sd = sounddevice
        self.engine = engine
        self.recognizer = sr.Recognizer()
        self.key_pressed = key_pressed
        self.pause = pause if pause else float(os.environ.get("FRIDAY_PAUSE") or DEFAULT_PAUSE)

        try:
            on_status("正在适应环境噪音,请保持安静 1 秒……")
            with self._open() as stream:
                noise = [rms(self._read(stream)) for _ in range(round(CALIBRATE_SECONDS / CHUNK_SECONDS))]
        except Exception as e:  # 没有麦克风、麦克风被占用或没有权限
            raise VoiceUnavailable(
                f"无法打开麦克风:{e}。请检查麦克风是否插好,"
                "以及 Windows“设置 → 隐私和安全性 → 麦克风”里是否允许桌面应用使用麦克风"
            ) from e
        self.noise = sum(noise) / len(noise) if noise else 0.0

        self._vosk = None
        if engine == "vosk":
            try:
                import vosk
            except ImportError as e:
                raise VoiceUnavailable(INSTALL_HINT) from e
            vosk.SetLogLevel(-1)
            self._vosk = vosk.Model(ensure_vosk_model(on_status=on_status))

    @property
    def start_level(self) -> float:
        return max(self.noise * START_RATIO, MIN_START_LEVEL)

    def _open(self):
        return self.sd.RawInputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16", blocksize=CHUNK)

    @staticmethod
    def _read(stream) -> bytes:
        data, _overflowed = stream.read(CHUNK)
        return bytes(data)

    def record(self, on_status: StatusFn = print) -> bytes | None:
        """录一句话,返回 16kHz 16 位单声道的原始音频;一直没开口时返回 None。"""
        hint = f"说完停顿 {self.pause:g} 秒" + (",或按回车结束" if os.name == "nt" else "")
        on_status(f"我在听,请说话……({hint})")
        wait_chunks = round(LISTEN_TIMEOUT / CHUNK_SECONDS)
        pause_chunks = max(1, round(self.pause / CHUNK_SECONDS))
        max_chunks = round(PHRASE_LIMIT / CHUNK_SECONDS)
        pre_roll = max(1, round(PRE_ROLL_SECONDS / CHUNK_SECONDS))
        self.key_pressed()  # 清掉之前残留的按键

        with self._open() as stream:  # 进入 with 时开始录音,退出时停止
            frames: list[bytes] = []
            # 等待开口;等待期间顺便跟上环境噪音的变化
            for _ in range(wait_chunks):
                chunk = self._read(stream)
                frames = (frames + [chunk])[-pre_roll:]
                level = rms(chunk)
                if level >= self.start_level:
                    break
                self.noise = self.noise * 0.95 + level * 0.05
                if self.key_pressed():
                    return None
            else:
                return None

            keep_level = self.start_level * KEEP_RATIO
            quiet = 0
            while len(frames) < max_chunks:
                chunk = self._read(stream)
                frames.append(chunk)
                quiet = quiet + 1 if rms(chunk) < keep_level else 0
                if quiet >= pause_chunks or self.key_pressed():
                    break
        return b"".join(frames)

    def listen(self, on_status: StatusFn = print) -> str | None:
        """录一句话,返回识别出的文字;没听到或没听清时返回 None。"""
        audio = self.record(on_status)
        if not audio:
            return None
        on_status("识别中……")
        if self._vosk is not None:
            return self._recognize_vosk(audio)
        sr = self.sr
        try:
            data = sr.AudioData(audio, SAMPLE_RATE, 2)
            return self.recognizer.recognize_google(data, language="zh-CN") or None
        except sr.UnknownValueError:
            return None
        except sr.RequestError as e:
            raise VoiceUnavailable(
                f"在线语音识别连接失败({e})。如果访问不了 Google,请改用离线识别:加参数 --stt vosk"
            ) from e

    def _recognize_vosk(self, audio: bytes) -> str | None:
        import vosk

        recognizer = vosk.KaldiRecognizer(self._vosk, SAMPLE_RATE)
        recognizer.AcceptWaveform(audio)
        text = json.loads(recognizer.FinalResult()).get("text", "")
        text = text.replace(" ", "")  # vosk 的中文结果字词之间带空格
        return text or None


class AudioKeepAlive:
    """在后台一直播放听不见的极小声音,让扬声器 / 蓝牙耳机保持唤醒。

    很多音频设备没声音时会休眠,来声音时要半秒到一秒才醒过来,
    这段时间播放的内容就丢了,听起来就是每句话的开头被吞掉。
    """

    BLOCK = 1024

    def __init__(self) -> None:
        import sounddevice as sd

        # 幅度只有 1(满幅是 32767),约 -90 分贝,人耳听不见,但设备会认为一直在放声音
        self._block = array.array("h", [1, 0, -1, 0] * (self.BLOCK // 4)).tobytes()
        self._stream = sd.RawOutputStream(
            samplerate=SAMPLE_RATE, channels=1, dtype="int16", blocksize=self.BLOCK, callback=self._fill
        )

    def _fill(self, outdata, frames, time_info, status) -> None:
        size = len(outdata)
        outdata[:] = (self._block * (size // len(self._block) + 1))[:size]

    def start(self) -> None:
        self._stream.start()

    def stop(self) -> None:
        try:
            self._stream.stop()
            self._stream.close()
        except Exception:
            pass


def start_keep_alive(on_status: StatusFn = print):
    """开始让音频设备保持唤醒;设置 FRIDAY_KEEP_AUDIO_AWAKE=0 可关闭。失败时不影响使用。"""
    if os.environ.get("FRIDAY_KEEP_AUDIO_AWAKE", "1").strip() in ("0", "false", "no", "off"):
        return None
    try:
        keep_alive = AudioKeepAlive()
        keep_alive.start()
        return keep_alive
    except Exception as e:
        on_status(f"(无法让音频设备保持唤醒:{e}。如果每句话开头被吞掉,可以检查扬声器设置)")
        return None


class VoiceIO:
    """把听和说组合在一起,供聊天循环使用。"""

    def __init__(self, listener, speaker, keep_alive=None) -> None:
        self.listener = listener
        self.speaker = speaker
        self.keep_alive = keep_alive

    def listen(self, on_status: StatusFn) -> str | None:
        return self.listener.listen(on_status)

    def speak(self, text: str) -> None:
        self.speaker.speak(text)


def _play_mp3_windows(path: str, winmm=None) -> None:
    """用 Windows 自带的媒体接口(MCI)播放 MP3,不需要额外安装播放器。"""
    import ctypes

    if winmm is None:
        winmm = ctypes.windll.winmm
    alias = "friday_tts"

    def mci(command: str) -> str:
        buf = ctypes.create_unicode_buffer(128)
        error = winmm.mciSendStringW(command, buf, 127, None)
        if error:
            raise RuntimeError(f"播放失败(MCI 错误 {error}):{command}")
        return buf.value

    mci(f'open "{path}" type mpegvideo alias {alias}')
    try:
        mci(f"set {alias} time format milliseconds")
        length_ms = int(mci(f"status {alias} length") or 0)
        mci(f"play {alias}")
        deadline = time.monotonic() + length_ms / 1000 + 10
        # 分段等待播放结束,这样按 Ctrl+C 能立刻停下
        while time.monotonic() < deadline and mci(f"status {alias} mode") in ("playing", "seeking", "not ready"):
            time.sleep(0.1)
    finally:
        winmm.mciSendStringW(f"close {alias}", None, 0, None)


def _find_mp3_player() -> list[str] | None:
    for command in (["afplay"], ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"], ["mpg123", "-q"]):
        if shutil.which(command[0]):
            return command
    return None


def play_mp3(path: str) -> None:
    if os.name == "nt":
        _play_mp3_windows(path)
        return
    player = _find_mp3_player()
    if player is None:
        raise RuntimeError("没有找到能播放 MP3 的程序(afplay / ffplay / mpg123)")
    subprocess.run(player + [path], check=True)


class EdgeSpeaker:
    """用微软 Edge“大声朗读”的神经网络语音朗读,声音自然,需要联网。

    联网失败时自动改用系统自带的语音读这一句,不会没有声音。
    """

    def __init__(
        self,
        voice: str = DEFAULT_EDGE_VOICE,
        fallback: Callable[[], object] | None = None,
        on_status: StatusFn = print,
        player: Callable[[str], None] = play_mp3,
    ) -> None:
        try:
            import edge_tts
        except ImportError as e:
            raise VoiceUnavailable(INSTALL_HINT) from e
        if os.name != "nt" and player is play_mp3 and _find_mp3_player() is None:
            raise VoiceUnavailable("没有找到能播放 MP3 的程序,请安装 ffmpeg 或 mpg123")
        self.edge_tts = edge_tts
        self.voice = resolve_edge_voice(voice)
        self.voice_name = f"{self.voice}(微软 Edge 在线语音)"
        self.voice_names = [f"{name} - {desc}" for name, desc in EDGE_VOICES.values()]
        self.has_chinese_voice = self.voice.lower().startswith("zh-")
        self._fallback_factory = fallback
        self._fallback = None
        self._warned = False
        self.on_status = on_status
        self.player = player

    def speak(self, text: str) -> None:
        text = clean_for_speech(text)
        if not text:
            return
        fd, path = tempfile.mkstemp(prefix="friday_", suffix=".mp3")
        os.close(fd)
        try:
            try:
                asyncio.run(self.edge_tts.Communicate(text, self.voice).save(path))
                if os.path.getsize(path) == 0:
                    raise RuntimeError("没有收到语音数据")
            except KeyboardInterrupt:
                raise
            except Exception as e:
                self._speak_with_fallback(text, e)
                return
            self.player(path)
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    def _speak_with_fallback(self, text: str, error: Exception) -> None:
        if not self._warned:
            self.on_status(f"在线语音暂时不可用({type(error).__name__}: {error}),先用系统自带的语音朗读。")
            self._warned = True
        if self._fallback is None and self._fallback_factory is not None:
            self._fallback = self._fallback_factory()
        if self._fallback is None:
            raise error
        self._fallback.speak(text)


# ======================== 语音消息(Discord):整段音频转文字、文字转 MP3 ========================

FFMPEG_HINT = "处理语音消息需要先安装依赖:python -m pip install -r assistant/requirements.txt"
_vosk_model = None


def decode_audio(data: bytes, rate: int = SAMPLE_RATE) -> bytes:
    """把任意格式的音频(Discord 语音消息是 ogg/opus)解码成 16 位单声道 PCM。

    用 imageio-ffmpeg 自带的 ffmpeg,不需要另外安装。
    """
    try:
        import imageio_ffmpeg
    except ImportError as e:
        raise VoiceUnavailable(FFMPEG_HINT) from e
    proc = subprocess.run(
        [imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
         "-f", "s16le", "-ac", "1", "-ar", str(rate), "pipe:1"],
        input=data, capture_output=True, timeout=120,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),  # 不闪黑色窗口
    )
    if proc.returncode != 0 or not proc.stdout:
        detail = proc.stderr.decode(errors="replace").strip()[-200:]
        raise RuntimeError(f"无法解码这段语音{(':' + detail) if detail else ''}")
    return proc.stdout


def transcribe_audio(data: bytes, engine: str | None = None) -> str | None:
    """把一整段音频转成文字;没听清时返回 None。engine:google(默认)或 vosk。"""
    engine = engine or os.environ.get("FRIDAY_STT") or "google"
    pcm = decode_audio(data)
    if engine == "vosk":
        global _vosk_model
        try:
            import vosk
        except ImportError as e:
            raise VoiceUnavailable(INSTALL_HINT) from e
        if _vosk_model is None:
            vosk.SetLogLevel(-1)
            _vosk_model = vosk.Model(ensure_vosk_model(on_status=lambda t: None))
        recognizer = vosk.KaldiRecognizer(_vosk_model, SAMPLE_RATE)
        recognizer.AcceptWaveform(pcm)
        return json.loads(recognizer.FinalResult()).get("text", "").replace(" ", "") or None
    try:
        import speech_recognition as sr
    except ImportError as e:
        raise VoiceUnavailable(FFMPEG_HINT) from e
    try:
        return sr.Recognizer().recognize_google(sr.AudioData(pcm, SAMPLE_RATE, 2), language="zh-CN") or None
    except sr.UnknownValueError:
        return None
    except sr.RequestError as e:
        raise VoiceUnavailable(f"在线语音识别连接失败({e})。可以在 .env 里写 FRIDAY_STT=vosk 改用离线识别") from e


def synthesize_mp3(text: str, edge_voice: str | None = None, max_chars: int = 800) -> bytes | None:
    """用微软 Edge 神经网络语音把文字转成 MP3;失败或没有可读内容时返回 None。"""
    text = clean_for_speech(text)
    if not text:
        return None
    if len(text) > max_chars:  # 太长的回答只读前面一段,完整内容看文字
        cut = max(text.rfind("。", 0, max_chars), text.rfind("\n", 0, max_chars))
        text = text[: cut + 1 if cut > max_chars // 2 else max_chars] + "……其余内容请看文字。"
    try:
        import edge_tts
    except ImportError:
        return None
    voice_name = resolve_edge_voice(edge_voice or os.environ.get("FRIDAY_TTS_VOICE"))
    fd, path = tempfile.mkstemp(prefix="friday_", suffix=".mp3")
    os.close(fd)
    try:
        asyncio.run(edge_tts.Communicate(text, voice_name).save(path))
        with open(path, "rb") as f:
            data = f.read()
        return data or None
    except Exception:
        return None
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def make_system_speaker():
    return WindowsSpeaker() if os.name == "nt" else Speaker()


def make_speaker(tts: str | None = None, edge_voice: str | None = None, on_status: StatusFn = print):
    """tts:edge(默认,在线神经网络语音)或 system(系统自带语音,离线)。"""
    tts = tts or os.environ.get("FRIDAY_TTS") or "edge"
    if tts == "system":
        return make_system_speaker()
    try:
        return EdgeSpeaker(
            edge_voice or os.environ.get("FRIDAY_TTS_VOICE") or DEFAULT_EDGE_VOICE,
            fallback=make_system_speaker,
            on_status=on_status,
        )
    except VoiceUnavailable as e:
        on_status(f"无法使用在线语音({e}),改用系统自带的语音。")
        return make_system_speaker()


def create_voice(
    engine: str = "google",
    on_status: StatusFn = print,
    tts: str | None = None,
    edge_voice: str | None = None,
    pause: float | None = None,
) -> VoiceIO:
    speaker = make_speaker(tts, edge_voice, on_status)
    if not speaker.has_chinese_voice:
        on_status(NO_CHINESE_VOICE_HINT)
    # 先唤醒音频设备;接下来校准麦克风的 1 秒正好留给设备醒过来
    keep_alive = start_keep_alive(on_status)
    return VoiceIO(Listener(engine, on_status, pause=pause), speaker, keep_alive)


def self_test() -> int:
    """检查朗读和麦克风:分别试读系统语音和在线语音,再录一句话。"""
    keep_alive = start_keep_alive(print)
    if keep_alive is not None:
        time.sleep(1)  # 给音频设备一点时间醒过来
    print("== 1. 系统语音(离线)==")
    try:
        speaker = make_system_speaker()
        print("系统里的朗读语音:")
        for name in speaker.voice_names or ["(一个都没有)"]:
            print(f"  - {name}")
        print(f"正在使用:{speaker.voice_name}")
        if not speaker.has_chinese_voice:
            print(NO_CHINESE_VOICE_HINT)
        print("现在会读一句:“你好,我是星期五,这是系统自带的声音。”")
        speaker.speak("你好,我是星期五,这是系统自带的声音。")
        print("读完了。")
    except VoiceUnavailable as e:
        print(f"系统语音不可用:{e}")

    print("\n== 2. 在线语音(更自然)==")
    try:
        edge = EdgeSpeaker(fallback=None)
        print(f"正在使用:{edge.voice_name}")
        print("现在会读一句:“你好,我是星期五,这是更自然的在线声音。”")
        edge.speak("你好,我是星期五,这是更自然的在线声音。")
        print("读完了。如果没听到声音,请检查音量和默认的扬声器/耳机。")
    except Exception as e:
        print(f"在线语音不可用:{type(e).__name__}: {e}")

    print("\n== 3. 麦克风测试 ==")
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
