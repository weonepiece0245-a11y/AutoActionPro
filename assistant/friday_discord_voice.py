"""Discord 语音频道里的星期五:你在 Discord 上说“进语音”,她就进你现在所在的语音频道;
在频道里说“星期五,……”她就用声音回答(频道里的人都听得到)。说“退出语音”或你离开频道,她就走。

  - 只听配对过的账号(你)的声音,朋友叫她不理;
  - “星期五”这个词在电脑上用离线识别(Vosk)听,声音不传出去;听到了才把那一句送去识别、回答;
  - 要你“允许”的事在语音频道里一律不做(频道里有别人);手机遥控上了锁的话,要先私信验证码解锁;
  - 第一次用会自动安装语音组件(PyNaCl、discord-ext-voice-recv、vosk)并下载约 42MB 的中文离线模型。
"""

from __future__ import annotations

import array
import io
import json
import re
import threading
import time
import wave
from typing import Callable

WAKE = "星期五"
JOIN_RE = re.compile(r"^(星期五)?[,， ]*(进|来|加入|进入|到)(我的?)?(语音|语音频道|频道|房间|vc|VC)(来|里)?(吧|一下)?$")
LEAVE_RE = re.compile(r"^(星期五)?[,， ]*(退出|离开|出去|走吧?|滚)(语音|语音频道|频道|vc|VC)?(吧|了)?$")
GAP_SECONDS = 0.8  # 不说话这么久算一句话说完
MAX_PHRASE = 15.0
MIN_PHRASE = 0.4
FOLLOW_SECONDS = 8.0  # 只说“星期五”时,这么久内的下一句当成问题
STEREO_48K = 48000 * 2 * 2  # 每秒字节数


def to_16k_mono(pcm: bytes) -> bytes:
    """Discord 的 48kHz 双声道 16 位 -> 16kHz 单声道(语音识别用)。左右声道平均,每 3 个取平均。"""
    samples = array.array("h")
    samples.frombytes(pcm[: len(pcm) // 4 * 4])
    out = array.array("h")
    for i in range(0, len(samples) - 5, 6):  # 3 帧 × 2 声道
        total = samples[i] + samples[i + 1] + samples[i + 2] + samples[i + 3] + samples[i + 4] + samples[i + 5]
        out.append(max(-32768, min(32767, total // 6)))
    return out.tobytes()


def wav_16k(mono: bytes) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(mono)
    return buffer.getvalue()


class Segmenter:
    """把一个人一小段一小段送来的声音拼成一句话:停顿 0.8 秒,或超过 15 秒,就算说完。"""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self.buffer = bytearray()
        self.last = 0.0
        self.lock = threading.Lock()

    def feed(self, pcm: bytes) -> bytes | None:
        with self.lock:
            self.buffer.extend(pcm)
            self.last = self.clock()
            if len(self.buffer) >= MAX_PHRASE * STEREO_48K:
                return self._take()
        return None

    def poll(self) -> bytes | None:
        with self.lock:
            if self.buffer and self.clock() - self.last >= GAP_SECONDS:
                return self._take()
        return None

    def _take(self) -> bytes | None:
        phrase, self.buffer = bytes(self.buffer), bytearray()
        return phrase if len(phrase) >= MIN_PHRASE * STEREO_48K else None


def strip_wake(text: str) -> str:
    """“星期五,今天天气怎么样” -> “今天天气怎么样”。"""
    at = text.find(WAKE)
    rest = text[at + len(WAKE):] if at >= 0 else text
    return rest.strip(" ,，。.!！?？、:：")


class VoiceBrain:
    """一句话进来以后怎么办(和 Discord 分开,方便测试)。

    heard_wake(pcm16k) -> 有没有说“星期五”(离线);transcribe(wav) -> 文字;ask(问题) -> 回答;say(文字) 在频道里读。
    """

    def __init__(self, heard_wake: Callable[[bytes], bool], transcribe: Callable[[bytes], str | None],
                 ask: Callable[[str], str | None], say: Callable[[str], None], locked: Callable[[], bool] = lambda: False,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.heard_wake, self.transcribe, self.ask, self.say = heard_wake, transcribe, ask, say
        self.locked = locked
        self.clock = clock
        self.follow_until = 0.0  # 只叫了“星期五”:等下一句

    def phrase(self, pcm48: bytes) -> str | None:
        """处理一句话;问了问题返回回答(没叫她就 None)。"""
        mono = to_16k_mono(pcm48)
        following = self.clock() < self.follow_until
        if not following and not self.heard_wake(mono):
            return None  # 没叫星期五:不送去识别
        self.follow_until = 0.0
        if self.locked():
            self.say("手机遥控上锁了。先私信我验证器上的 6 位数字,再叫我。")
            return None
        text = self.transcribe(wav_16k(mono)) or ""
        question = text if following else strip_wake(text)
        if not question:
            if following or WAKE in text or not text:
                self.follow_until = self.clock() + FOLLOW_SECONDS
                self.say("我在。")
            return None
        reply = self.ask(question)
        if reply:
            self.say(reply)
        return reply


def vosk_wake_checker(model_loader: Callable[[], object]) -> Callable[[bytes], bool]:
    """用 Vosk 在电脑上听一句话里有没有“星期五”(和语音唤醒同一个模型)。"""
    import friday_wake

    state: dict = {}

    def check(mono16k: bytes) -> bool:
        if "model" not in state:
            state["vosk"], state["model"] = model_loader()
        recognizer = state["vosk"].KaldiRecognizer(state["model"], 16000,
                                                    json.dumps(friday_wake.grammar(WAKE), ensure_ascii=False))
        recognizer.SetWords(True)
        recognizer.AcceptWaveform(mono16k)
        confidence = friday_wake.wake_confidence(recognizer.FinalResult(), WAKE)
        return confidence is not None and confidence >= 0.5
    return check


def load_vosk(on_status: Callable[[str], None]):
    try:
        import vosk
    except ImportError:
        import friday_wake

        on_status("第一次用语音频道:正在安装离线识别组件……")
        if not friday_wake.pip_install(["vosk"]):
            raise RuntimeError("离线识别组件没装好,检查一下网络")
        import vosk
    import voice

    vosk.SetLogLevel(-1)
    return vosk, vosk.Model(voice.ensure_vosk_model(on_status=on_status))


def ensure_voice_packages(on_status: Callable[[str], None]) -> None:
    try:
        import nacl  # noqa: F401
        from discord.ext import voice_recv  # noqa: F401
    except ImportError:
        import friday_wake

        on_status("第一次用语音频道:正在安装 Discord 语音组件(一两分钟)……")
        if not friday_wake.pip_install(["PyNaCl", "discord-ext-voice-recv"]):
            raise RuntimeError("Discord 语音组件没装好,检查一下网络")


# ======================== 接到 Discord ========================


class DiscordVoice:
    """在 Discord 机器人的线程里进出语音频道、收你的声音、播回答。"""

    def __init__(self, bridge, brain_factory: Callable[[Callable[[str], None]], VoiceBrain],
                 synthesize: Callable[[str], bytes | None], on_status: Callable[[str], None]) -> None:
        self.bridge = bridge
        self.brain_factory = brain_factory
        self.synthesize = synthesize
        self.on_status = on_status
        self.vc = None
        self.owner_id: int | None = None
        self.segmenter = Segmenter()
        self.running = False

    # ---------- 进、出 ----------
    async def join(self, owner_id: int) -> str:
        ensure_voice_packages(self.on_status)
        from discord.ext import voice_recv

        client = self.bridge.client
        target = None
        for guild in client.guilds:
            for channel in list(guild.voice_channels) + list(getattr(guild, "stage_channels", [])):
                if owner_id in getattr(channel, "voice_states", {}):
                    target = channel
        if target is None:
            return "你现在不在任何语音频道里。先进一个语音频道,再叫我“进语音”。"
        if self.vc is not None and self.vc.is_connected():
            await self.vc.disconnect(force=True)
        self.vc = await target.connect(cls=voice_recv.VoiceRecvClient)
        self.owner_id = owner_id
        brain = self.brain_factory(self.say)
        segmenter = self.segmenter
        voice = self

        class Sink(voice_recv.AudioSink):
            def wants_opus(self) -> bool:
                return False

            def write(self, user, data) -> None:
                if user is None or getattr(user, "id", None) != voice.owner_id:
                    return  # 只听你的
                phrase = segmenter.feed(data.pcm)
                if phrase:
                    voice._handle(brain, phrase)

            def cleanup(self) -> None:
                pass

        self.vc.listen(Sink())
        self.running = True
        threading.Thread(target=lambda: self._watch(brain), daemon=True, name="friday-discord-voice").start()
        return f"我进 {target.name} 了。叫“星期五”再说问题就行;只听你的声音。说“退出语音”我就走。"

    async def leave(self) -> str:
        self.running = False
        if self.vc is not None and self.vc.is_connected():
            await self.vc.disconnect(force=True)
            self.vc = None
            return "我退出语音频道了。"
        return "我本来就不在语音频道里。"

    def owner_left(self, member_id: int, after_channel) -> bool:
        """你离开了她在的频道:要不要跟着走。"""
        return (self.vc is not None and member_id == self.owner_id
                and (after_channel is None or after_channel != getattr(self.vc, "channel", None)))

    # ---------- 听、说 ----------
    def _watch(self, brain: VoiceBrain) -> None:
        while self.running:
            phrase = self.segmenter.poll()
            if phrase:
                self._handle(brain, phrase)
            time.sleep(0.2)

    def _handle(self, brain: VoiceBrain, phrase: bytes) -> None:
        threading.Thread(target=lambda: self._safe(brain, phrase), daemon=True).start()

    def _safe(self, brain: VoiceBrain, phrase: bytes) -> None:
        try:
            brain.phrase(phrase)
        except Exception as e:  # noqa: BLE001
            self.on_status(f"语音频道这句没处理好:{type(e).__name__}: {e}")

    def say(self, text: str) -> None:
        """在语音频道里读出来(大家都听得到)。"""
        audio = self.synthesize(text) if self.synthesize else None
        if not audio or self.vc is None or self.bridge.loop is None:
            return
        import discord
        import imageio_ffmpeg

        def play() -> None:
            if self.vc is None or not self.vc.is_connected():
                return
            if self.vc.is_playing():
                self.vc.stop()
            source = discord.FFmpegPCMAudio(io.BytesIO(audio), pipe=True, executable=imageio_ffmpeg.get_ffmpeg_exe())
            self.vc.play(source)
        self.bridge.loop.call_soon_threadsafe(play)
