"""游戏和家里的灯联动(Tapo,不花钱):
  开游戏时:选好的灯调暗、换氛围色或关掉;打完(离开游戏 90 秒)改回原来的样子。
  离开电脑一阵子:选好的灯和插座自动关掉;回来时(可以选)再开回来。只关它自己关掉的,不会乱开你本来关着的。
在 齿轮设定 → 游戏 → 家里的灯联动 里选。电脑睡眠、关机后星期五就不能动了,所以离开时间要比 Windows 的睡眠时间短。
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Callable

GAME_MODES = {"none": "不动", "dim": "调暗(20%)", "off": "关掉", "红": "红色", "紫": "紫色", "蓝": "蓝色", "青": "青色",
              "绿": "绿色", "橙": "橙色", "粉": "粉色"}
AWAY_MINUTES = (0, 10, 20, 30, 60)
SEP = "|"


def names_setting(key: str) -> list[str]:
    raw = os.environ.get(key, "")
    return [n.strip() for n in raw.split(SEP) if n.strip()][:30]


def clean_names(items) -> list[str]:
    if isinstance(items, str):
        items = items.split(SEP)
    names = []
    for item in items or []:
        name = " ".join(str(item).split())[:40]
        if name and SEP not in name and name not in names:
            names.append(name)
    return names[:30]


def settings() -> dict:
    mode = os.environ.get("FRIDAY_LIGHT_GAME_MODE", "none")
    try:
        away = int(os.environ.get("FRIDAY_LIGHT_AWAY_MIN", "0") or 0)
    except ValueError:
        away = 0
    return {"game_mode": mode if mode in GAME_MODES else "none", "game": names_setting("FRIDAY_LIGHT_GAME"),
            "away_minutes": away if away in AWAY_MINUTES else 0, "away": names_setting("FRIDAY_LIGHT_AWAY"),
            "back": os.environ.get("FRIDAY_LIGHT_BACK", "on") != "off"}


class LightLink:
    def __init__(self, home: Callable[[], object], config: Callable[[], dict] = settings,
                 path: str | os.PathLike | None = None) -> None:
        self.home = home  # () -> friday_tapo.TapoHome 或 None(还没连 Tapo)
        self.config = config
        self.lock = threading.Lock()
        self.path = Path(path) if path else None  # 记在文件里:星期五中途重开(例如半夜自动更新)也改得回来
        self.saved: dict[str, dict] = {}  # 开游戏前灯的样子:id -> {on, brightness, hsv, color_temp}
        self.turned_off: list[str] = []  # 离开时被星期五关掉的
        self.away_done = False  # 这次离开已经处理过了(不用每分钟再去找一次设备)
        if self.path is not None:
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                self.saved = {str(k): v for k, v in (data.get("saved") or {}).items() if isinstance(v, dict)}
                self.turned_off = [str(i) for i in data.get("turned_off") or []]
                self.away_done = bool(self.turned_off)
            except (OSError, ValueError, AttributeError):
                pass

    def _save(self) -> None:
        if self.path is None:
            return
        try:
            if self.saved or self.turned_off:
                self.path.write_text(json.dumps({"saved": self.saved, "turned_off": self.turned_off},
                                                ensure_ascii=False), encoding="utf-8")
            else:
                self.path.unlink(missing_ok=True)
        except OSError:
            pass

    def _targets(self, names: list[str]) -> tuple[object, list[dict]]:
        home = self.home()
        if home is None or not names:
            return None, []
        try:
            devices = home.refresh(force=True)
        except Exception:
            return None, []
        wanted = {n.lower() for n in names}
        return home, [d for d in devices if d["name"].lower() in wanted]

    # ---------- 打游戏 ----------
    def game_start(self) -> str:
        cfg = self.config()
        mode = cfg["game_mode"]
        if mode == "none":
            return ""
        with self.lock:
            if self.saved:
                return ""
            home, targets = self._targets(cfg["game"])
            if home is None or not targets:
                return ""
            import friday_tapo

            done = []
            for d in targets:
                if not d.get("on") and mode != "off":
                    continue  # 本来关着的灯,打游戏也不帮你开
                self.saved[d["id"]] = {k: d.get(k) for k in ("on", "brightness", "hsv", "color_temp", "name")}
                try:
                    if mode == "off" or d["kind"] != "light":
                        if mode == "off" and d.get("on"):
                            home.backend.set_on(d["id"], False)
                            d["on"] = False
                        else:
                            self.saved.pop(d["id"], None)
                            continue
                    elif mode == "dim":
                        home.backend.set_brightness(d["id"], 20)
                    else:
                        hue, saturation = friday_tapo.COLORS[mode]
                        home.backend.set_hsv(d["id"], hue, saturation, min(int(d.get("brightness") or 60), 60))
                    done.append(d["name"])
                except Exception:
                    self.saved.pop(d["id"], None)
            self._save()
            if not done:
                return ""
            return f"开游戏了,{'、'.join(done)}{'关掉了' if mode == 'off' else '调暗了' if mode == 'dim' else '换成' + mode + '色了'}。"

    def game_end(self) -> str:
        with self.lock:
            saved, self.saved = self.saved, {}
            home = self.home() if saved else None
            if home is None:
                self.saved = saved  # Tapo 还没连上:下次再改回去
                return ""
            self._save()
            back = []
            for device_id, old in saved.items():
                try:
                    if old.get("color_temp"):  # 原来是白光(彩色模式时色温读出来是 0)
                        home.backend.set_color_temp(device_id, old["color_temp"])
                    elif old.get("hsv"):
                        hue, saturation, value = old["hsv"]
                        home.backend.set_hsv(device_id, hue, saturation, value)
                    if old.get("brightness"):
                        home.backend.set_brightness(device_id, old["brightness"])
                    if old.get("on"):
                        home.backend.set_on(device_id, True)
                    back.append(old.get("name") or device_id)
                except Exception:
                    continue
            return f"游戏打完了,{'、'.join(back)}改回原来的样子。" if back else ""

    # ---------- 离开电脑 ----------
    def away_tick(self, idle: float | None) -> str:
        """每分钟看一次你离开多久了。"""
        cfg = self.config()
        if idle is None:
            return ""
        with self.lock:
            if cfg["away_minutes"] and idle >= cfg["away_minutes"] * 60 and not self.away_done:
                home, targets = self._targets(cfg["away"])
                if home is None:
                    return ""  # Tapo 还没连上:下一分钟再试
                self.away_done = True
                for d in targets:
                    if not d.get("on"):
                        continue
                    try:
                        home.backend.set_on(d["id"], False)
                        d["on"] = False
                        self.turned_off.append(d["id"])
                    except Exception:
                        continue
                self._save()
                if self.turned_off:
                    names = [d["name"] for d in targets if d["id"] in self.turned_off]
                    return f"你离开电脑 {cfg['away_minutes']} 分钟了,帮你关掉了 {'、'.join(names)}。"
                return ""
            if idle < 60:
                self.away_done = False
            if self.turned_off and idle < 60:
                ids, self.turned_off = self.turned_off, []
                self._save()
                if not cfg["back"]:
                    return ""
                home = self.home()
                if home is None:
                    return ""
                back = []
                for device_id in ids:
                    try:
                        home.backend.set_on(device_id, True)
                        back.append(next((d["name"] for d in home.devices if d["id"] == device_id), device_id))
                    except Exception:
                        continue
                return f"欢迎回来,{'、'.join(back)}开回来了。" if back else ""
            return ""
