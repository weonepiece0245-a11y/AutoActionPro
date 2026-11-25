"""一键开黑模式:说“开黑”,星期五一次做好:打开 Discord、Steam(或你选的程序)、启动你选的游戏、
家里的灯换氛围色、Spotify 放你的游戏歌单、开启别打扰;说“不玩了”全部恢复。

在 齿轮设定 → 游戏 → 一键开黑 里选要做哪些。灯用“家里的灯联动”里选的那几盏。不花钱。
"""

from __future__ import annotations

import os
import re
import threading
from typing import Callable

START_RE = re.compile(r"^(星期五)?[,， ]*(我要|来|开始|准备)?开黑(模式)?(了|吧|啦)?$|^(打开|开启|开)开黑模式$")
STOP_RE = re.compile(r"^(星期五)?[,， ]*(不玩了|开黑结束|结束开黑|关掉开黑模式|关闭开黑模式|下线了|打完了|收工)(了|吧|啦)?$")
DEFAULT_APPS = "Discord, Steam"


def settings() -> dict:
    apps = [a.strip() for a in re.split(r"[,,、]", os.environ.get("FRIDAY_PARTY_APPS", DEFAULT_APPS)) if a.strip()]
    return {
        "apps": apps[:6],
        "game": os.environ.get("FRIDAY_PARTY_GAME", "").strip()[:40],
        "playlist": os.environ.get("FRIDAY_PARTY_PLAYLIST", "").strip()[:60],
        "lights": os.environ.get("FRIDAY_PARTY_LIGHTS", "none").strip() or "none",
        "quiet": os.environ.get("FRIDAY_PARTY_QUIET", "on") != "off",
        "close": os.environ.get("FRIDAY_PARTY_CLOSE", "off") == "on",
    }


def clean_name(name: str) -> str:
    """程序/游戏/歌单名:只留一般的字,不能有路径、命令。"""
    name = " ".join(str(name or "").split())[:60]
    return name if re.fullmatch(r"[0-9A-Za-z一-鿿 ._+&'!:()\-]*", name) else ""


class PartyMode:
    def __init__(self, computer: Callable[[], object], spotify: Callable[[], object] = lambda: None,
                 lights: Callable[[], object] = lambda: None, care: Callable[[], object] = lambda: None,
                 config: Callable[[], dict] = settings) -> None:
        self.computer, self.spotify, self.lights, self.care = computer, spotify, lights, care
        self.config = config
        self.active = False
        self.opened: list[str] = []
        self.started_music = False
        self.lock = threading.Lock()

    def start(self) -> str:
        """一步一步做;哪一步没做成就跳过,最后告诉你做了什么。"""
        with self.lock:
            cfg = self.config()
            done, failed = [], []
            computer = self.computer()
            self.opened = []
            if computer is not None and getattr(computer, "available", False):
                for app in cfg["apps"]:
                    app = clean_name(app)
                    if not app:
                        continue
                    try:
                        result = computer.do("open_app", app)
                    except Exception as e:  # noqa: BLE001
                        result = str(e)
                    (done if result.startswith("已打开") or "本来就在运行" in result else failed).append(app)
                    if result.startswith("已打开"):
                        self.opened.append(app)
                game = clean_name(cfg["game"])
                if game:
                    try:
                        result = computer.do("open_game", game)
                        (done if result.startswith(("已通过", "已打开")) else failed).append(game)
                    except Exception:  # noqa: BLE001
                        failed.append(game)
            link = self.lights()
            if link is not None and cfg["lights"] != "none":
                original = link.config
                link.config = lambda: {**original(), "game_mode": cfg["lights"]}
                try:
                    if link.game_start():
                        done.append("灯")
                except Exception:  # noqa: BLE001
                    failed.append("灯")
                finally:
                    link.config = original
            playlist = clean_name(cfg["playlist"])
            spotify = self.spotify()
            if playlist and spotify is not None and getattr(spotify, "connected", False):
                try:
                    result = spotify.play("歌单 " + playlist)
                    ok = result.startswith("好")
                    (done if ok else failed).append(f"歌单《{playlist}》")
                    self.started_music = ok
                except Exception:  # noqa: BLE001
                    failed.append(f"歌单《{playlist}》")
            care = self.care()
            if cfg["quiet"] and care is not None:
                care.forced_quiet = True
                done.append("别打扰")
            self.active = True
        text = "开黑模式开好了:" + ("、".join(done) if done else "没有要做的(去齿轮设定 → 游戏 → 一键开黑 选要做哪些)")
        if failed:
            text += f";{'、'.join(failed)} 没弄好"
        return text + "。打完说“不玩了”我就全部恢复。"

    def stop(self) -> str:
        with self.lock:
            if not self.active:
                return "现在没有在开黑模式。"
            cfg = self.config()
            done = []
            link = self.lights()
            if link is not None:
                try:
                    if link.game_end():
                        done.append("灯改回来了")
                except Exception:  # noqa: BLE001
                    pass
            spotify = self.spotify()
            if self.started_music and spotify is not None:
                try:
                    spotify.control("pause")
                    done.append("音乐停了")
                except Exception:  # noqa: BLE001
                    pass
            care = self.care()
            if care is not None and getattr(care, "forced_quiet", False):
                care.forced_quiet = False
                done.append("别打扰关了")
            computer = self.computer()
            if cfg["close"] and self.opened and computer is not None:
                try:
                    closed = computer.close_programs([a.lower() for a in self.opened])
                    if closed:
                        done.append("关掉了 " + "、".join(closed))
                except Exception:  # noqa: BLE001
                    pass
            self.active, self.opened, self.started_music = False, [], False
        return "好,开黑结束:" + ("、".join(done) if done else "没有要恢复的") + "。辛苦了!"
