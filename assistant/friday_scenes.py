"""场景模式:一句话做好几件事(在电脑上直接做,不问 Claude,不花钱)。

每个场景有名字、触发的说法、步骤。步骤用中文一行写一步,例如:
    说 晚安,老板
    静音
    关掉 chrome, spotify
    锁屏
能用的步骤见 STEP_HELP。为了安全,不支持关机、重启、睡眠、执行命令。
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Callable

MAX_SCENES = 12
MAX_STEPS = 15
STEP_HELP = ("说 一句话 / 音量 30 / 静音 / 暂停音乐 / 下一首 / 打开 程序名 / 打开网址 https://… / "
             "关掉 程序1, 程序2 / 游戏模式 / 锁屏 / 显示桌面 / 深色模式 / 浅色模式 / 等 3 秒")

DEFAULT_SCENES = [
    {"name": "睡觉", "triggers": ["我要睡了", "我去睡了", "晚安", "睡觉模式"],
     "steps": "说 晚安,老板,早点休息。\n等 4 秒\n静音\n锁屏"},
    {"name": "开工", "triggers": ["开工", "开始工作", "上班模式"],
     "steps": "说 开工了,今天也加油。\n音量 30\n打开网址 https://mail.google.com"},
]

# (正则, 动作);动作:(computer 操作, 值) 或特殊的 say / wait / close
STEP_PATTERNS = [
    (r"^(说|讲)\s*(?P<v>.+)$", "say"),
    (r"^音量\s*(调到|调成|设为)?\s*(?P<v>\d{1,3})\s*%?$", "volume"),
    (r"^静音$", "silence"),
    (r"^(暂停音乐|暂停|播放音乐|继续播放)$", "play_pause"),
    (r"^(下一首|切歌)$", "next_track"),
    (r"^打开网址\s*(?P<v>https?://\S+)$", "open_url"),
    (r"^打开\s*(?P<v>[^\s].{0,40})$", "open_app"),
    (r"^(关掉|关闭)\s*(?P<v>.+)$", "close"),
    (r"^游戏模式$", "game_mode"),
    (r"^(锁屏|锁定屏幕)$", "lock_screen"),
    (r"^显示桌面$", "show_desktop"),
    (r"^深色模式$", "dark_mode"),
    (r"^浅色模式$", "light_mode"),
    (r"^等\s*(?P<v>\d{1,2})\s*秒?$", "wait"),
]


def parse_steps(text: str) -> list[tuple[str, str]]:
    """一行一步 -> [(动作, 值)];看不懂的行抛 ValueError(说明是哪一行)。"""
    steps = []
    for number, raw in enumerate(str(text or "").splitlines(), 1):
        line = " ".join(raw.split()).strip()
        if not line or line.startswith("#"):
            continue
        for pattern, action in STEP_PATTERNS:
            found = re.match(pattern, line)
            if found:
                value = (found.groupdict().get("v") or "").strip()
                if action == "volume" and int(value) > 100:
                    raise ValueError(f"第 {number} 行:音量要在 0~100 之间")
                if action == "wait" and int(value) > 10:
                    raise ValueError(f"第 {number} 行:最多等 10 秒")
                steps.append((action, value))
                break
        else:
            raise ValueError(f"第 {number} 行看不懂:“{line}”。可以用:{STEP_HELP}")
    if not steps:
        raise ValueError("场景里没有步骤")
    if len(steps) > MAX_STEPS:
        raise ValueError(f"一个场景最多 {MAX_STEPS} 步")
    return steps


def _clean_trigger(text: str) -> str:
    return re.sub(r"[\s,,。.!!??~～、]+", "", str(text or "")).lower()


class Scenes:
    def __init__(self, path: str | os.PathLike) -> None:
        self.path = Path(path)
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            scenes = data.get("scenes") if isinstance(data, dict) else None
        except (OSError, ValueError):
            scenes = None
        self.scenes: list[dict] = scenes if isinstance(scenes, list) else [dict(s) for s in DEFAULT_SCENES]

    def _save(self) -> None:
        temp = self.path.with_name(self.path.name + ".tmp")
        temp.write_text(json.dumps({"scenes": self.scenes}, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(temp, self.path)

    def set_scene(self, name: str, triggers, steps: str, old_name: str = "") -> str:
        name = " ".join(str(name or "").split())[:20]
        if not name:
            raise ValueError("场景要有名字")
        if isinstance(triggers, str):
            triggers = re.split(r"[,,、;;\n]+", triggers)
        triggers = [t.strip()[:20] for t in triggers if _clean_trigger(t)][:8]
        if not triggers:
            raise ValueError("要给至少一种说法,例如“我要睡了”")
        parse_steps(steps)  # 先检查看不看得懂
        others = [s for s in self.scenes if s["name"] not in (name, old_name)]
        taken = {_clean_trigger(t): s["name"] for s in others for t in s["triggers"]}
        for trigger in triggers:
            if _clean_trigger(trigger) in taken:
                raise ValueError(f"“{trigger}”已经是“{taken[_clean_trigger(trigger)]}”场景的说法了")
        if len(others) >= MAX_SCENES:
            raise ValueError(f"最多 {MAX_SCENES} 个场景")
        scene = {"name": name, "triggers": triggers, "steps": str(steps).strip()}
        index = next((i for i, s in enumerate(self.scenes) if s["name"] in (name, old_name)), None)
        if index is None:
            self.scenes.append(scene)
        else:
            self.scenes[index] = scene
        self._save()
        return f"场景“{name}”存好了,说“{triggers[0]}”就会执行。"

    def remove(self, name: str) -> str:
        before = len(self.scenes)
        self.scenes = [s for s in self.scenes if s["name"] != name]
        if len(self.scenes) == before:
            raise ValueError(f"没有“{name}”这个场景")
        self._save()
        return f"删掉了场景“{name}”"

    def find(self, text: str) -> dict | None:
        """说的话对得上哪个场景(说法完全一样,或“XX模式”“开启XX”)。"""
        said = _clean_trigger(text)
        said = re.sub(r"^(开启|进入|打开|启动|切换到?)", "", said)
        for scene in self.scenes:
            names = [scene["name"], scene["name"] + "模式"] + list(scene["triggers"])
            if said in {_clean_trigger(n) for n in names}:
                return scene
        return None

    def describe(self) -> str:
        if not self.scenes:
            return "还没有场景"
        return "\n".join(f"「{s['name']}」说法:{'、'.join(s['triggers'])};步骤:{s['steps'].replace(chr(10), ' → ')}"
                         for s in self.scenes)


def run(scene: dict, computer, sleep: Callable[[float], None] | None = None,
        on_say: Callable[[str], None] | None = None) -> tuple[str, list[str]]:
    """执行一个场景 -> (要说的话, 做了哪些事)。某一步失败就记下来、继续下一步。

    on_say:“说”的那一步马上读出来(这样“说 晚安”再“静音”也听得到);没有就最后一起返回。
    """
    import friday_care

    sleep = sleep or time.sleep
    said, done = [], []
    for action, value in parse_steps(scene["steps"]):
        try:
            if action == "say":
                if on_say is not None:
                    on_say(value)
                    done.append(f"说了“{value}”")
                else:
                    said.append(value)
                continue
            if action == "wait":
                sleep(int(value))
                continue
            if computer is None or not computer.available:
                done.append(f"{action}(这台电脑不能操作)")
                continue
            if action == "volume":
                computer.do("volume_down", "100")
                if int(value):
                    computer.do("volume_up", value)
                done.append(f"音量 {value}%")
            elif action == "silence":
                computer.do("volume_down", "100")
                done.append("静音")
            elif action == "close":
                names = [friday_care.clean_program_name(n) for n in re.split(r"[,,、\s]+", value)]
                names = [n for n in names if n and n not in friday_care.NEVER_CLOSE]
                closed = computer.close_programs(names)
                done.append("关掉了 " + "、".join(closed) if closed else f"{'、'.join(names)} 本来就没开")
            else:
                result = computer.do(action, value)
                done.append(result.rstrip("。"))
        except Exception as e:
            done.append(f"{action} 没做成({type(e).__name__})")
    return " ".join(said), done
