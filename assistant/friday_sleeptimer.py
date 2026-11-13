"""睡前定时:“30 分钟后停音乐”“听完这首就停”“1 小时后让电脑睡眠/关机”(不花钱)。

Spotify 在放的话,最后 60 秒会把音量慢慢调小再暂停(停完把音量调回原来的,下次放歌不会没声音);
别的播放器就按“停止”键。让电脑睡眠或关机要你先同意。说“取消定时”就不停。
"""

from __future__ import annotations

import datetime
import threading
import time
from typing import Callable

FADE_SECONDS = 60
MAX_MINUTES = 12 * 60
KINDS = {"music": "停音乐", "song": "这首放完就停", "sleep": "让电脑睡眠", "shutdown": "关机"}


def _hm(stamp: float) -> str:
    return datetime.datetime.fromtimestamp(stamp).strftime("%H:%M")


def _left(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds} 秒"
    minutes = round(seconds / 60)
    return f"{minutes // 60} 小时 {minutes % 60} 分钟" if minutes >= 60 else f"{minutes} 分钟"


class SleepTimer:
    def __init__(self, spotify: Callable[[], object] = lambda: None, computer: Callable[[], object] = lambda: None,
                 clock: Callable[[], float] = time.time) -> None:
        self.spotify = spotify  # () -> friday_spotify.Spotify(没连就是 None)
        self.computer = computer  # () -> friday_pc.Computer
        self.clock = clock
        self.lock = threading.RLock()
        self.job: dict | None = None  # {"kind", "at", "volume"(渐弱前的音量), "fading"}
        self.on_change: Callable[[dict], None] = lambda state: None  # 告诉窗口(倒计时)
        self.on_done: Callable[[str], None] = lambda text: None  # 到点做完了

    # ---------- 设定 ----------
    def state(self) -> dict:
        job = self.job
        return {"at": job["at"] if job else 0, "label": KINDS.get(job["kind"], "") if job else ""}

    def _set(self, kind: str, at: float) -> None:
        with self.lock:
            self.job = {"kind": kind, "at": at, "volume": None, "fading": False}
        self.on_change(self.state())

    def set(self, kind: str, minutes: float | None = None) -> str:
        if kind not in KINDS:
            raise ValueError("只能定时停音乐、这首放完就停、让电脑睡眠或关机")
        if kind == "song":
            return self._set_song()
        try:
            minutes = float(minutes)
        except (TypeError, ValueError):
            raise ValueError("要说几分钟后") from None
        if not 1 <= minutes <= MAX_MINUTES:
            raise ValueError("定时要在 1 分钟到 12 小时之间")
        at = self.clock() + minutes * 60
        if kind == "shutdown":
            computer = self.computer()
            if computer is None:
                raise ValueError("这台电脑不能定时关机")
            computer.do("shutdown", str(int(round(minutes))))  # Windows 自己会准时关,星期五关了也照样关
        self._set(kind, at)
        tail = {"music": "到时候把音乐停掉(Spotify 会先慢慢变小声)",
                "sleep": "到时候先停音乐,再让电脑睡眠",
                "shutdown": "到时候电脑会关机,记得先存好文件"}[kind]
        return f"好,{_left(minutes * 60)}后({_hm(at)}){tail}。说“取消定时”就不停。"

    def _set_song(self) -> str:
        spotify = self.spotify()
        if spotify is None or not getattr(spotify, "connected", False):
            raise ValueError("要连上 Spotify 才知道这首歌还剩多久;可以改说“10 分钟后停音乐”")
        status, data = spotify.api("GET", "/me/player/currently-playing")
        item = data.get("item") if status == 200 and isinstance(data, dict) else None
        if not item or not data.get("is_playing"):
            raise ValueError("Spotify 现在没在放歌")
        left = max(1.0, (item.get("duration_ms", 0) - data.get("progress_ms", 0)) / 1000)
        self._set("song", self.clock() + left + 1)
        self.job["track"] = item.get("uri")
        return f"好,《{item.get('name', '这首歌')}》放完(还有 {_left(left)})就停。"

    def cancel(self) -> str:
        with self.lock:
            job, self.job = self.job, None
        if job is None:
            return "现在没有定时。"
        if job["kind"] == "shutdown":
            computer = self.computer()
            if computer is not None:
                computer.do("cancel_shutdown")
        if job.get("volume") is not None:
            self._volume(job["volume"])  # 正在变小声:调回来
        self.on_change(self.state())
        return f"好,取消了“{KINDS[job['kind']]}”的定时。"

    def status(self) -> str:
        job = self.job
        if job is None:
            return "现在没有定时。说“30 分钟后停音乐”就能设。"
        return f"{_left(job['at'] - self.clock())}后({_hm(job['at'])}){KINDS[job['kind']]}。"

    # ---------- 到点 ----------
    def _spotify_playing(self) -> tuple[object, dict] | None:
        spotify = self.spotify()
        if spotify is None or not getattr(spotify, "connected", False):
            return None
        try:
            status, data = spotify.api("GET", "/me/player")
        except Exception:
            return None
        if status == 200 and isinstance(data, dict) and data.get("is_playing"):
            return spotify, data
        return None

    def _volume(self, percent: int) -> None:
        spotify = self.spotify()
        if spotify is None:
            return
        try:
            spotify.api("PUT", "/me/player/volume", {"volume_percent": max(0, min(100, int(percent)))})
        except Exception:
            pass

    def _stop_music(self, volume: int | None) -> None:
        playing = self._spotify_playing()
        if playing is not None:
            spotify, _ = playing
            try:
                spotify.api("PUT", "/me/player/pause")
            except Exception:
                pass
            if volume is not None:
                time.sleep(0.5)
                self._volume(volume)  # 停了再把音量调回来:下次放歌不会没声音
            return
        computer = self.computer()
        if computer is not None:
            try:
                computer.do("stop_media")  # 别的播放器:按“停止”键(不是播放/暂停,免得把停着的音乐打开)
            except Exception:
                pass

    def tick(self) -> str | None:
        """每秒看一次;做完了返回做了什么。"""
        with self.lock:
            job = self.job
            if job is None:
                return None
            left = job["at"] - self.clock()
            kind = job["kind"]
            if kind in ("music", "song", "sleep") and 0 < left <= FADE_SECONDS:
                if not job["fading"]:
                    job["fading"] = True
                    playing = self._spotify_playing()
                    if playing is not None:
                        job["volume"] = (playing[1].get("device") or {}).get("volume_percent")
                if job.get("volume"):
                    target = int(job["volume"] * left / FADE_SECONDS)
                    if job.get("last_set") is None or job["last_set"] - target >= 5:
                        job["last_set"] = target
                        self._volume(target)
                return None
            if left > 0:
                return None
            self.job = None
        self.on_change(self.state())
        if kind == "shutdown":
            text = "定时到了,电脑在关机。"
        else:
            self._stop_music(job.get("volume"))
            text = "定时到了,音乐停了。晚安。"
            if kind == "sleep":
                computer = self.computer()
                if computer is not None:
                    try:
                        computer.do("sleep")
                    except Exception:
                        pass
                text = "定时到了,音乐停了,电脑去睡觉了。晚安。"
        self.on_done(text)
        return text

    def start(self, interval: float = 1.0) -> None:
        def loop() -> None:
            while True:
                try:
                    self.tick()
                except Exception:
                    pass
                time.sleep(interval)

        threading.Thread(target=loop, daemon=True, name="friday-sleep-timer").start()
