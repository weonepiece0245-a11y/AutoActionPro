"""电脑的主音量:读、设(Windows 自带的音量接口,不用装别的,不按音量键)。

按住说话时把电脑声音(游戏、音乐、浏览器)先压低,说完放开再恢复,识别得更准。
"""

from __future__ import annotations

import os
import threading
from typing import Callable

DUCK_TO = 0.3  # 默认压到原来的 30%
DUCK_CHOICES = (10, 20, 30, 40, 50, 60)


def duck_to() -> float:
    """按住说话时压到原来的几成(齿轮设定里选 10%~60%)。"""
    try:
        percent = int(os.environ.get("FRIDAY_PTT_DUCK_TO", "30"))
    except ValueError:
        percent = 30
    return (percent if percent in DUCK_CHOICES else 30) / 100


def duck_enabled() -> bool:
    return os.environ.get("FRIDAY_PTT_DUCK", "on") != "off"


class WindowsMasterVolume:
    """用 Core Audio 的 IAudioEndpointVolume 读、设默认播放设备的主音量(0~1)。"""

    CLSID_ENUMERATOR = "{BCDE0395-E52F-467C-8E3D-C4579291692E}"
    IID_ENUMERATOR = "{A95664D2-9614-4F35-A746-DE8DB63617E6}"
    IID_ENDPOINT_VOLUME = "{5CDF2C82-841E-4546-9722-0CF74078229A}"

    def __init__(self) -> None:
        import ctypes

        self.ctypes = ctypes
        self.ole32 = ctypes.OleDLL("ole32")

    def _guid(self, text: str):
        ctypes = self.ctypes

        class GUID(ctypes.Structure):
            _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort), ("Data3", ctypes.c_ushort),
                        ("Data4", ctypes.c_ubyte * 8)]
        guid = GUID()
        self.ole32.CLSIDFromString(ctypes.c_wchar_p(text), ctypes.byref(guid))
        return guid

    def _release(self, obj) -> None:
        ctypes = self.ctypes
        if obj:
            vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
            ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtable[2])(obj)

    def _endpoint(self):
        ctypes = self.ctypes
        try:
            self.ole32.CoInitializeEx(None, 0x2)  # 这个线程第一次用 COM(已经初始化过也没关系)
        except OSError:
            pass
        enumerator = ctypes.c_void_p()
        self.ole32.CoCreateInstance(ctypes.byref(self._guid(self.CLSID_ENUMERATOR)), None, 23,
                                    ctypes.byref(self._guid(self.IID_ENUMERATOR)), ctypes.byref(enumerator))
        device = ctypes.c_void_p()
        get_default = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                                         ctypes.POINTER(ctypes.c_void_p))
        vtable = ctypes.cast(enumerator, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        found = get_default(vtable[4])(enumerator, 0, 1, ctypes.byref(device))  # eRender, eMultimedia
        self._release(enumerator)
        if found != 0:
            raise OSError("找不到播放设备")
        volume = ctypes.c_void_p()
        activate = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p,
                                      ctypes.POINTER(ctypes.c_void_p))
        vtable = ctypes.cast(device, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        opened = activate(vtable[3])(device, ctypes.byref(self._guid(self.IID_ENDPOINT_VOLUME)), 23, None,
                                     ctypes.byref(volume))
        self._release(device)
        if opened != 0:
            raise OSError("打不开音量控制")
        return volume

    # ---------- 所有正在用的播放设备(耳机、喇叭、显示器……)----------
    def _each_volume(self):
        """[(设备编号, IAudioEndpointVolume)]:每个“已启用”的播放设备。用完要 _release。"""
        ctypes = self.ctypes
        try:
            self.ole32.CoInitializeEx(None, 0x2)
        except OSError:
            pass
        enumerator = ctypes.c_void_p()
        self.ole32.CoCreateInstance(ctypes.byref(self._guid(self.CLSID_ENUMERATOR)), None, 23,
                                    ctypes.byref(self._guid(self.IID_ENUMERATOR)), ctypes.byref(enumerator))
        table = lambda obj: ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents  # noqa: E731
        collection = ctypes.c_void_p()
        enum = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_int, ctypes.c_ulong,
                                  ctypes.POINTER(ctypes.c_void_p))
        status = enum(table(enumerator)[3])(enumerator, 0, 1, ctypes.byref(collection))  # eRender, DEVICE_STATE_ACTIVE
        self._release(enumerator)
        if status != 0:
            raise OSError("列不出播放设备")
        count = ctypes.c_uint()
        ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint))(table(collection)[3])(
            collection, ctypes.byref(count))
        item = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p))
        get_id = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(ctypes.c_wchar_p))
        activate = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p,
                                      ctypes.POINTER(ctypes.c_void_p))
        found = []
        for i in range(count.value):
            device = ctypes.c_void_p()
            if item(table(collection)[4])(collection, i, ctypes.byref(device)) != 0:
                continue
            ident = ctypes.c_wchar_p()
            get_id(table(device)[5])(device, ctypes.byref(ident))
            name = ident.value or str(i)
            if ident:
                ctypes.windll.ole32.CoTaskMemFree(ident)
            volume = ctypes.c_void_p()
            if activate(table(device)[3])(device, ctypes.byref(self._guid(self.IID_ENDPOINT_VOLUME)), 23, None,
                                          ctypes.byref(volume)) == 0:
                found.append((name, volume))
            self._release(device)
        self._release(collection)
        return found

    def levels(self) -> dict[str, float]:
        ctypes = self.ctypes
        getter = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(ctypes.c_float))
        result = {}
        for name, volume in self._each_volume():
            level = ctypes.c_float()
            try:
                vtable = ctypes.cast(volume, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
                if getter(vtable[9])(volume, ctypes.byref(level)) == 0:
                    result[name] = float(level.value)
            finally:
                self._release(volume)
        return result

    def set_levels(self, levels: dict[str, float]) -> None:
        ctypes = self.ctypes
        setter = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_float, ctypes.c_void_p)
        for name, volume in self._each_volume():
            try:
                if name in levels:
                    vtable = ctypes.cast(volume, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
                    setter(vtable[7])(volume, ctypes.c_float(max(0.0, min(1.0, levels[name]))), None)
            finally:
                self._release(volume)

    def get(self) -> float:
        ctypes = self.ctypes
        volume = self._endpoint()
        level = ctypes.c_float()
        vtable = ctypes.cast(volume, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        getter = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(ctypes.c_float))
        try:
            getter(vtable[9])(volume, ctypes.byref(level))  # GetMasterVolumeLevelScalar
        finally:
            self._release(volume)
        return float(level.value)

    def set(self, level: float) -> None:
        ctypes = self.ctypes
        volume = self._endpoint()
        vtable = ctypes.cast(volume, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        setter = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_float, ctypes.c_void_p)
        try:
            setter(vtable[7])(volume, ctypes.c_float(max(0.0, min(1.0, level))), None)  # SetMasterVolumeLevelScalar
        finally:
            self._release(volume)


class PycawVolume:
    """后备做法:用 pycaw(很多程序在用的 Windows 音量组件,第一次用时自动安装)读、设主音量。"""

    def __init__(self) -> None:
        try:
            import pycaw  # noqa: F401
        except ImportError:
            import friday_wake

            if not friday_wake.pip_install(["pycaw", "comtypes"]):
                raise RuntimeError("音量组件 pycaw 没装好,检查一下网络") from None

    @staticmethod
    def _endpoint():
        import comtypes
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

        try:
            comtypes.CoInitialize()
        except OSError:
            pass
        speakers = AudioUtilities.GetSpeakers()
        if hasattr(speakers, "EndpointVolume"):  # 新版 pycaw
            return speakers.EndpointVolume
        from ctypes import POINTER, cast

        from comtypes import CLSCTX_ALL

        interface = speakers.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        return cast(interface, POINTER(IAudioEndpointVolume))

    def get(self) -> float:
        return float(self._endpoint().GetMasterVolumeLevelScalar())

    def set(self, level: float) -> None:
        self._endpoint().SetMasterVolumeLevelScalar(max(0.0, min(1.0, float(level))), None)


class FallbackVolume:
    """先用自己的做法;那个在你电脑上出错的话,换 pycaw,之后一直用能用的那个。"""

    def __init__(self, makers=None) -> None:
        self.makers = list(makers) if makers is not None else [WindowsMasterVolume, PycawVolume]
        self.current = None
        self.errors: list[str] = []

    def _try(self, action):
        while True:
            if self.current is None:
                if not self.makers:
                    raise RuntimeError(";".join(self.errors) or "调不了电脑音量")
                try:
                    self.current = self.makers.pop(0)()
                except Exception as e:  # noqa: BLE001
                    self.errors.append(f"{type(e).__name__}: {e}")
                    continue
            try:
                return action(self.current)
            except Exception as e:  # noqa: BLE001
                self.errors.append(f"{type(self.current).__name__} {type(e).__name__}: {e}")
                print(f"电脑音量:{self.errors[-1]},换下一个做法", flush=True)
                self.current = None

    def get(self) -> float:
        return self._try(lambda v: v.get())

    def set(self, level: float) -> None:
        self._try(lambda v: v.set(level))

    def levels(self) -> dict[str, float]:
        """每个播放设备的音量;做法只认得默认设备时,就只有那一个。"""
        return self._try(lambda v: v.levels() if hasattr(v, "levels") else {"默认": v.get()})

    def set_levels(self, levels: dict[str, float]) -> None:
        if not levels:
            return
        self._try(lambda v: v.set_levels(levels) if hasattr(v, "set_levels")
                  else (v.set(levels["默认"]) if "默认" in levels else None))


class Ducker:
    """按住说话时压低电脑声音,放开后恢复成原来的大小(只恢复它自己压低的)。"""

    def __init__(self, volume=None, enabled: Callable[[], bool] = duck_enabled,
                 on_error: Callable[[str], None] = lambda text: None) -> None:
        self.volume = volume
        self.enabled = enabled
        self.on_error = on_error  # 压不低时告诉你原因(只说一次,免得每按一次都说)
        self.saved: float | None = None
        self.lowered_to: float | None = None
        self.error = ""
        self.lock = threading.Lock()
        self.log: list[str] = []  # 最近几次的经过(“按住说话诊断”看这个)

    def _note(self, text: str) -> None:
        import time

        self.log = (self.log + [f"{time.strftime('%H:%M:%S')} {text}"])[-8:]

    def _vol(self):
        if self.volume is None:
            self.volume = FallbackVolume()
        return self.volume

    def _levels(self) -> dict[str, float]:
        vol = self._vol()
        return vol.levels() if hasattr(vol, "levels") else {"默认": vol.get()}

    def _set_levels(self, levels: dict[str, float]) -> None:
        if not levels:
            return  # 没有要改的(不能当成 0)
        vol = self._vol()
        if hasattr(vol, "set_levels"):
            vol.set_levels(levels)
        elif "默认" in levels:
            vol.set(levels["默认"])

    def duck(self) -> None:
        """所有正在用的播放设备(耳机、喇叭……)一起压低:声音从哪个设备出来都会变小。"""
        if not self.enabled():
            self._note("按下:压低声音的开关是关着的,没压")
            return
        with self.lock:
            try:
                levels = self._levels()
                if not levels or max(levels.values()) <= 0.02:
                    self._note(f"按下:音量本来就很小({', '.join(f'{v:.0%}' for v in levels.values()) or '没有设备'}),不用压")
                    return
                self.saved = levels
                self.lowered_to = {k: round(v * duck_to(), 3) for k, v in levels.items()}
                self._set_levels(self.lowered_to)
                after = self._levels()
                self._note(f"按下:{len(levels)} 个播放设备 " + "、".join(
                    f"{levels[k]:.0%}→{after.get(k, -1):.0%}" for k in levels) + f"(线程 {threading.current_thread().name})")
                self.error = ""
            except Exception as e:  # noqa: BLE001
                self.saved = None
                text = f"{type(e).__name__}: {e}"
                self._note(f"按下:出错 {text}")
                print(f"按住说话没压低电脑声音:{text}", flush=True)
                if text != self.error:
                    self.error = text
                    try:
                        self.on_error(f"没压低电脑声音({text})")
                    except Exception:
                        pass

    def restore(self) -> None:
        with self.lock:
            if self.saved is None:
                return
            saved, lowered, self.saved, self.lowered_to = self.saved, self.lowered_to or {}, None, None
            try:
                now = self._levels()
                back = {}
                for name, level in saved.items():
                    if name in now and abs(now[name] - lowered.get(name, now[name])) > 0.05:
                        continue  # 你在说话时自己调过这个设备的音量:听你的
                    back[name] = level
                self._set_levels(back)
                self._note(f"放开:恢复 {len(back)} 个设备")
            except Exception as e:  # noqa: BLE001
                self._note(f"放开:恢复时出错 {type(e).__name__}: {e}")

def test_duck(volume=None, wait: Callable[[float], None] | None = None) -> str:
    """“测试压低声音”:所有播放设备压低两秒再恢复,告诉你每个设备的音量(不用按住说话也能试)。"""
    import time

    wait = wait or time.sleep
    vol = volume or FallbackVolume()
    ducker = Ducker(vol, enabled=lambda: True)
    try:
        before = ducker._levels()
    except Exception as e:  # noqa: BLE001
        return f"读不到电脑的音量:{type(e).__name__}: {e}。把这句截图给我。"
    if not before or max(before.values()) <= 0.02:
        return "电脑现在是静音或音量很小,先把音量调大一点再试。"
    ducker.duck()
    if ducker.saved is None:
        return f"调不了电脑的音量:{ducker.error or '不知道为什么'}。把这句截图给我。"
    lowered = ducker._levels()
    wait(2.0)
    ducker.restore()
    after = ducker._levels()
    parts = [f"{before[k]:.0%}→{lowered.get(k, -1):.0%}→{after.get(k, -1):.0%}" for k in before]
    stuck = [k for k in before if abs(lowered.get(k, before[k]) - before[k] * duck_to()) > 0.03]
    if stuck:
        return f"有 {len(stuck)} 个播放设备的音量没压下去(" + "、".join(parts) + ")。把这句截图给我。"
    return (f"压低声音正常:{len(before)} 个播放设备都压低两秒再恢复(" + "、".join(parts)
            + ")。你刚才有听到声音变小吗?")
