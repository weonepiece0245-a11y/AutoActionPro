"""电脑的主音量:读、设(Windows 自带的音量接口,不用装别的,不按音量键)。

按住说话时把电脑声音(游戏、音乐、浏览器)先压低,说完放开再恢复,识别得更准。
"""

from __future__ import annotations

import os
import threading
from typing import Callable

DUCK_TO = 0.3  # 压到原来的 30%


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


class Ducker:
    """按住说话时压低电脑声音,放开后恢复成原来的大小(只恢复它自己压低的)。"""

    def __init__(self, volume=None, enabled: Callable[[], bool] = duck_enabled) -> None:
        self.volume = volume
        self.enabled = enabled
        self.saved: float | None = None
        self.lowered_to: float | None = None
        self.lock = threading.Lock()

    def _vol(self):
        if self.volume is None:
            self.volume = WindowsMasterVolume()
        return self.volume

    def duck(self) -> None:
        if not self.enabled():
            return
        with self.lock:
            try:
                level = self._vol().get()
                if level <= 0.02:
                    return  # 本来就很小声/静音
                self.saved = level
                self.lowered_to = round(level * DUCK_TO, 3)
                self._vol().set(self.lowered_to)
            except Exception:
                self.saved = None

    def restore(self) -> None:
        with self.lock:
            if self.saved is None:
                return
            saved, lowered, self.saved, self.lowered_to = self.saved, self.lowered_to, None, None
            try:
                now = self._vol().get()
                if lowered is not None and abs(now - lowered) > 0.05:
                    return  # 你在说话时自己调过音量:听你的,不改回去
                self._vol().set(saved)
            except Exception:
                pass
