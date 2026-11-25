"""Discord 语音频道里的星期五:你在 Discord 上说“进语音”,她就进你现在所在的语音频道;
在频道里说“星期五,……”她就用声音回答(频道里的人都听得到)。说“退出语音”或你离开频道,她就走。

  - 只听配对过的账号(你)的声音,朋友叫她不理;
  - “星期五”这个词在电脑上用离线识别(Vosk)听,声音不传出去;听到了才把那一句送去识别、回答;
  - 要你“允许”的事在语音频道里一律不做(频道里有别人);手机遥控上了锁的话,要先私信验证码解锁;
  - 第一次用会自动安装语音组件(PyNaCl、discord-ext-voice-recv、vosk)并下载约 42MB 的中文离线模型。
"""

from __future__ import annotations

import array
import asyncio
import importlib
import io
import json
import queue
import re
import threading
import time
import wave
from typing import Callable

WAKE = "星期五"
LOAD_RETRY = 600  # 离线识别模型载入失败:10 分钟后才再试
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


WAKE_RE = re.compile(r"星期五|星期5|礼拜五|禮拜五|friday", re.I)  # 识别出来的“星期五”常见写法


def has_wake(text: str) -> bool:
    return WAKE_RE.search(str(text or "")) is not None


def strip_wake(text: str) -> str:
    """“星期五,今天天气怎么样” -> “今天天气怎么样”。"""
    found = WAKE_RE.search(text)
    rest = text[found.end():] if found else text
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
        if not following and text and not has_wake(text):
            return None  # 离线听错以为叫了她,识别出来其实没有“星期五”:不把你跟朋友说的话拿去回答
        question = text if following else strip_wake(text)
        if not question:
            if following or has_wake(text) or not text:
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
    lock = threading.Lock()

    def check(mono16k: bytes) -> bool:
        if "model" not in state:
            with lock:  # 刚进频道连说两句:两句同时到,模型(和第一次安装)只做一次
                if "model" not in state:
                    if time.monotonic() - state.get("failed_at", -1e9) < LOAD_RETRY:
                        return False  # 刚刚才失败过:先不重试
                    try:
                        state["vosk"], state["model"] = model_loader()
                    except Exception:
                        state["failed_at"] = time.monotonic()
                        raise
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
        importlib.invalidate_caches()  # 刚装好的:Python 才找得到
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
        importlib.invalidate_caches()


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
        self._stop: threading.Event | None = None  # 这次连线的停止信号(换频道 / 退出时旧的线程就停)
        self.session = 0  # 第几次连线:旧频道算完的回答,不会在新频道读出来
        self.phrases: queue.Queue = queue.Queue()

    def _start_workers(self, brain: VoiceBrain) -> threading.Event:
        """停掉上一次连线的线程,开这次的:一个看有没有说完的话,一个按顺序处理(回答不会乱序、互相打断)。"""
        if self._stop is not None:
            self._stop.set()
        stop = threading.Event()
        self._stop = stop
        self.phrases = queue.Queue()
        self.running = True
        threading.Thread(target=lambda: self._watch(brain, stop), daemon=True, name="friday-discord-voice").start()
        threading.Thread(target=lambda: self._work(stop), daemon=True, name="friday-discord-voice-work").start()
        return stop

    # ---------- 进、出 ----------
    async def join(self, owner_id: int) -> str:
        # 第一次要 pip 安装(一两分钟):放到别的线程,不然整个 Discord 机器人会卡住、断线
        try:
            await asyncio.to_thread(ensure_voice_packages, self.on_status)
        except RuntimeError as e:
            return f"进不了语音频道:{e}"
        from discord.ext import voice_recv

        client = self.bridge.client
        target = None
        for guild in client.guilds:
            for channel in list(guild.voice_channels) + list(getattr(guild, "stage_channels", [])):
                if owner_id in getattr(channel, "voice_states", {}):
                    target = channel
        if target is None:
            return "你现在不在任何语音频道里。先进一个语音频道,再叫我“进语音”。"
        self._end_session()  # 旧的连线:线程停掉、没读完的回答作废
        old, self.vc = self.vc, None
        if old is not None and old.is_connected():
            await old.disconnect(force=True)
        try:
            self.vc = await target.connect(cls=voice_recv.VoiceRecvClient)
        except Exception as e:  # noqa: BLE001  没权限、超时、网络
            self.vc = None
            return f"进不了 {target.name}:{type(e).__name__}: {e}。看看机器人有没有“连接”和“说话”的权限,或者等一下再试。"
        self.owner_id = owner_id
        session = self.session
        brain = self.brain_factory(lambda text: self.say(text, session))
        self.segmenter = Segmenter()  # 上一个频道没说完的不要带过来
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
        self._start_workers(brain)
        return f"我进 {target.name} 了。叫“星期五”再说问题就行;只听你的声音。说“退出语音”我就走。"

    def _end_session(self) -> None:
        self.running = False
        if self._stop is not None:
            self._stop.set()
        self.session += 1
        self.segmenter = Segmenter()

    async def leave(self) -> str:
        self._end_session()
        vc, self.vc = self.vc, None
        if vc is not None and vc.is_connected():
            await vc.disconnect(force=True)
            return "我退出语音频道了。"
        return "我本来就不在语音频道里。"

    def owner_left(self, member_id: int, after_channel) -> bool:
        """你离开了她在的频道:要不要跟着走。"""
        return (self.vc is not None and member_id == self.owner_id
                and (after_channel is None or after_channel != getattr(self.vc, "channel", None)))

    # ---------- 听、说 ----------
    def _watch(self, brain: VoiceBrain, stop: threading.Event) -> None:
        while not stop.is_set():
            phrase = self.segmenter.poll()
            if phrase:
                self._handle(brain, phrase)
            stop.wait(0.2)

    def _handle(self, brain: VoiceBrain, phrase: bytes) -> None:
        self.phrases.put((brain, phrase))  # 排队:一句处理完再下一句

    def _work(self, stop: threading.Event) -> None:
        phrases = self.phrases
        while not stop.is_set():
            try:
                brain, phrase = phrases.get(timeout=0.5)
            except queue.Empty:
                continue
            self._safe(brain, phrase)

    def _safe(self, brain: VoiceBrain, phrase: bytes) -> None:
        try:
            brain.phrase(phrase)
        except Exception as e:  # noqa: BLE001
            self.on_status(f"语音频道这句没处理好:{type(e).__name__}: {e}")

    def say(self, text: str, session: int | None = None) -> None:
        """在语音频道里读出来(大家都听得到)。session:这句是哪一次连线的;换了频道就不读(别的频道可能是别人)。"""
        if session is not None and session != self.session:
            return
        audio = self.synthesize(text) if self.synthesize else None
        if not audio or self.vc is None or self.bridge.loop is None or (session is not None and session != self.session):
            return
        import discord
        import imageio_ffmpeg

        def play() -> None:
            if self.vc is None or not self.vc.is_connected() or (session is not None and session != self.session):
                return
            if self.vc.is_playing():
                self.vc.stop()
            source = discord.FFmpegPCMAudio(io.BytesIO(audio), pipe=True, executable=imageio_ffmpeg.get_ffmpeg_exe())
            self.vc.play(source)
        self.bridge.loop.call_soon_threadsafe(play)
