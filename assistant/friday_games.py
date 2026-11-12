"""自动认识新游戏:像 NTE 这种星期五不认识的游戏,不用全屏也认得(不花钱)。

怎么认:
  1. 装在 Steam(steamapps\\common)、Epic Games、Xbox(XboxGames)、Riot Games、EA Games、Ubisoft 游戏文件夹里的程序,
     一开就算游戏,名字用游戏文件夹的名字;
  2. 不认识的程序全屏 5 分钟以上,记成游戏(右下角会告诉你),以后窗口模式也认得;
  3. 说“刚才那个是游戏”“NTE 是游戏”(用你说的名字)、“刚才那个不是游戏”“XX 不是游戏”来教它。
存在 friday_games.json。
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path

LEARN_SECONDS = 5 * 60  # 不认识的程序全屏这么久,记成游戏
GAME_FOLDERS = (  # 路径里有这些(小写比较),就是装游戏的地方;后面那一层文件夹是游戏名
    "\\steamapps\\common\\", "\\epic games\\", "\\xboxgames\\", "\\riot games\\", "\\ea games\\",
    "\\ubisoft game launcher\\games\\", "\\gog galaxy\\games\\", "\\battle.net\\", "/steamapps/common/",
)
LAUNCHERS = {  # 游戏平台、启动器本身不算游戏
    "steam.exe", "steamwebhelper.exe", "epicgameslauncher.exe", "eadesktop.exe", "origin.exe", "battle.net.exe",
    "upc.exe", "ubisoftconnect.exe", "galaxyclient.exe", "riotclientservices.exe", "riotclientux.exe",
    "crashreportclient.exe", "unitycrashhandler64.exe", "easyanticheat_eos_setup.exe",
}
SYSTEM_APPS = {  # Windows 自己的小工具:不会是“刚才那个游戏”
    "taskmgr.exe", "explorer.exe", "searchhost.exe", "searchapp.exe", "shellexperiencehost.exe",
    "startmenuexperiencehost.exe", "applicationframehost.exe", "lockapp.exe", "systemsettings.exe", "cmd.exe",
    "powershell.exe", "pwsh.exe", "windowsterminal.exe", "notepad.exe", "snippingtool.exe", "screenclippinghost.exe",
    "textinputhost.exe", "claude.exe", "pythonw.exe", "python.exe",
}
THIS = ("", "这", "它", "这个", "那个", "刚才", "刚刚", "现在", "刚才的", "刚刚的", "刚才那个", "刚刚那个", "现在这个", "这个程序")
NAME_OK = re.compile(r"^[\w\-. ()'&:!一-鿿]{1,30}$")


def _exe(name: str) -> str:
    name = os.path.basename(str(name or "").strip().strip('"')).lower()
    return name if name.endswith(".exe") else (name + ".exe" if name else "")


def folder_game(path: str | None) -> str | None:
    """装在游戏文件夹里的程序 -> 游戏名(那一层文件夹的名字);不是就 None。"""
    if not path:
        return None
    lower = str(path).lower()
    if os.path.basename(lower) in LAUNCHERS or "launcher" in os.path.basename(lower):
        return None
    for marker in GAME_FOLDERS:
        at = lower.find(marker)
        if at >= 0:
            rest = str(path)[at + len(marker):]
            name = re.split(r"[\\/]", rest, maxsplit=1)[0].strip()
            return name or None
    return None


class KnownGames:
    def __init__(self, path: str | os.PathLike, clock=time.time) -> None:
        self.path = Path(path)
        self.clock = clock
        self.lock = threading.Lock()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        data = data if isinstance(data, dict) else {}
        self.games: dict[str, str] = {str(k).lower(): str(v) for k, v in (data.get("games") or {}).items()}
        self.not_games: set[str] = {str(x).lower() for x in data.get("not_games") or []}
        self.full_since: dict[str, float] = {}  # 不认识的程序从什么时候开始全屏
        self.recent: list[tuple[str, str | None]] = []  # 最近在最前面的程序(exe, 完整路径),给“刚才那个”用

    def _save(self) -> None:
        temp = self.path.with_name(f"{self.path.name}.{threading.get_ident()}.tmp")
        temp.write_text(json.dumps({"games": self.games, "not_games": sorted(self.not_games)}, ensure_ascii=False,
                                   indent=1), encoding="utf-8")
        os.replace(temp, self.path)

    # ---------- 认 ----------
    def name(self, exe: str, path: str | None = None) -> str | None:
        """认识的游戏 -> 名字(学过的、装在游戏文件夹里的);不认识 None。"""
        exe = (exe or "").lower()
        if not exe or exe in self.not_games:
            return None
        if exe in self.games:
            return self.games[exe]
        return folder_game(path)

    def is_not_game(self, exe: str) -> bool:
        return (exe or "").lower() in self.not_games

    def seen(self, exe: str, path: str | None, fullscreen: bool, not_games: set[str]) -> str | None:
        """每 30 秒看一次最前面的程序;不认识的全屏够久了就记成游戏,返回要告诉你的话。"""
        exe = (exe or "").lower()
        if not exe:
            return None
        if (exe not in not_games and exe not in LAUNCHERS and exe not in SYSTEM_APPS
                and (not self.recent or self.recent[-1][0] != exe)):
            self.recent = (self.recent + [(exe, path)])[-10:]
        if not fullscreen or exe in not_games or exe in LAUNCHERS or self.name(exe, path) or exe in self.not_games:
            self.full_since.pop(exe, None)
            return None
        now = self.clock()
        start = self.full_since.setdefault(exe, now)
        if now - start < LEARN_SECONDS:
            return None
        self.full_since.pop(exe, None)
        name = os.path.splitext(exe)[0]
        with self.lock:
            self.games[exe] = name
            self._save()
        return (f"记住了:{name} 是游戏,以后不用全屏也认得。不是游戏的话跟我说“{name} 不是游戏”;"
                f"想换个名字就说“{name} 叫做 新名字”。")

    # ---------- 教 ----------
    def _pick(self, words: str) -> str | None:
        """“刚才那个”/程序名/学过的游戏名 -> exe。"""
        words = str(words or "").strip().lower()
        if words in THIS or words.replace("这个", "").replace("那个", "") in THIS:
            return self.recent[-1][0] if self.recent else None
        exe = _exe(words)
        if exe in self.games or any(e == exe for e, _ in self.recent):
            return exe
        for known, name in self.games.items():
            if name.lower() == words or name.lower().replace(" ", "") == words.replace(" ", ""):
                return known
        for e, path in reversed(self.recent):
            if words in e or words in (folder_game(path) or "").lower():
                return e
        return None

    def mark_game(self, words: str) -> str:
        exe = self._pick(words)
        name = str(words or "").strip()
        if exe is None:
            if not self.recent:
                raise ValueError("还没看到你开过别的程序。先把游戏开起来,再跟我说“刚才那个是游戏”。")
            exe = self.recent[-1][0]  # “NTE 是游戏”:用你说的名字叫最近开的那个程序
        if name in THIS or name.replace("这个", "").replace("那个", "") in THIS or _exe(name).lower() == exe:
            name = self.games.get(exe) or folder_game(dict(self.recent).get(exe)) or os.path.splitext(exe)[0]
        if not NAME_OK.match(name):
            raise ValueError("名字太长或有奇怪的字")
        with self.lock:
            self.games[exe] = name
            self.not_games.discard(exe)
            self._save()
        return f"记住了:{name}({exe})是游戏。以后一开就自动游戏优化、提醒改成小通知。"

    def mark_not_game(self, words: str) -> str:
        exe = self._pick(words)
        if exe is None:
            raise ValueError(f"不知道“{words}”是哪个程序。可以说“刚才那个不是游戏”。")
        with self.lock:
            name = self.games.pop(exe, None) or os.path.splitext(exe)[0]
            self.not_games.add(exe)
            self._save()
        return f"好,{name}({exe})不是游戏,以后全屏也不当游戏。"

    def rename(self, old: str, new: str) -> str:
        exe = self._pick(old)
        new = str(new or "").strip()
        if exe is None or exe not in self.games:
            raise ValueError(f"没有叫“{old}”的游戏")
        if not NAME_OK.match(new):
            raise ValueError("名字太长或有奇怪的字")
        with self.lock:
            self.games[exe] = new
            self._save()
        return f"好,{exe} 以后叫 {new}。"

    def describe(self) -> str:
        if not self.games:
            return ("还没有学过的游戏。Apex、Dota 2 这些本来就认得;装在 Steam、Epic 文件夹里的也认得;"
                    "别的游戏开起来后跟我说“刚才那个是游戏”就行。")
        return "我学过的游戏:" + "、".join(f"{name}({exe})" for exe, name in self.games.items()) + "。"
