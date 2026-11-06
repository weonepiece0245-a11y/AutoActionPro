"""“星期五”操作电脑的能力(Windows)。

- Computer:一句话就能做的事,如调音量、切歌、截图、打开程序和游戏、切换耳机音箱、
  窗口管理、查看电脑状态、亮度和深色模式、找文件、关机睡眠等。
  会改变较多东西或不可撤销的操作(关机、关闭程序、清空回收站、运行程序文件……)要用户确认。
- ScreenControl:看屏幕、控制鼠标和键盘。每个问题第一次接管要用户同意;
  用户一动鼠标就停;不可撤销的操作单独确认;每个问题最多操作 MAX_ACTIONS 步。

真正和 Windows 打交道的代码都在 WindowsPC 里,其余逻辑可以用假的 backend 测试。
"""

from __future__ import annotations

import base64
import datetime
import io
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Callable

Approver = Callable[[str], bool]
Clock = Callable[[], datetime.datetime]


def _now() -> datetime.datetime:
    return datetime.datetime.now()


def _allow(_: str) -> bool:
    return True


class NeedsUser(Exception):
    """用户拒绝了,或用户接管了操作。"""


# ======================== 和 Windows 打交道的部分 ========================

VK = {
    "volume_up": 0xAF, "volume_down": 0xAE, "mute": 0xAD,
    "play_pause": 0xB3, "next_track": 0xB0, "previous_track": 0xB1,
}

KEY_CODES = {
    "ctrl": 0x11, "control": 0x11, "alt": 0x12, "shift": 0x10, "win": 0x5B, "windows": 0x5B,
    "enter": 0x0D, "return": 0x0D, "tab": 0x09, "esc": 0x1B, "escape": 0x1B, "space": 0x20,
    "backspace": 0x08, "delete": 0x2E, "del": 0x2E, "insert": 0x2D,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "capslock": 0x14, "printscreen": 0x2C,
    **{f"f{i}": 0x6F + i for i in range(1, 13)},
}
EXTENDED_KEYS = {0x2E, 0x2D, 0x26, 0x28, 0x25, 0x27, 0x24, 0x23, 0x21, 0x22}
DANGEROUS_COMBOS = {"alt+f4", "shift+delete", "shift+del", "ctrl+alt+delete", "ctrl+alt+del"}


def parse_keys(combo: str) -> list[int]:
    """把 "ctrl+shift+t" 这样的组合键转成虚拟键码列表。"""
    codes = []
    for part in combo.lower().replace(" ", "").split("+"):
        if not part:
            continue
        if part in KEY_CODES:
            codes.append(KEY_CODES[part])
        elif len(part) == 1 and part.isalnum():
            codes.append(ord(part.upper()))
        else:
            raise ValueError(f"不认识的按键:{part}")
    if not codes:
        raise ValueError("没有给出按键")
    return codes


def normalize_combo(combo: str) -> str:
    return combo.lower().replace(" ", "")


class WindowsPC:
    """调用 Windows 自带的功能。只在 Windows 上创建。"""

    def __init__(self) -> None:
        import ctypes

        self.ctypes = ctypes
        self.user32 = ctypes.windll.user32
        # 让坐标和截图都用真实像素,否则开了 125%/150% 缩放时会对不上
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                self.user32.SetProcessDPIAware()
            except Exception:
                pass

    # ---- 键盘 ----
    def press(self, vk: int, times: int = 1) -> None:
        for _ in range(times):
            self._key(vk, down=True)
            self._key(vk, down=False)

    def _key(self, vk: int, down: bool) -> None:
        flags = (0 if down else 2) | (1 if vk in EXTENDED_KEYS else 0)  # KEYUP=2, EXTENDEDKEY=1
        self.user32.keybd_event(vk, 0, flags, 0)

    def hotkey(self, codes: list[int]) -> None:
        for vk in codes:
            self._key(vk, down=True)
            time.sleep(0.02)
        for vk in reversed(codes):
            self._key(vk, down=False)

    def type_text(self, text: str) -> None:
        """逐字输入,支持中文(用 Unicode 方式发送,不依赖输入法)。"""
        ctypes = self.ctypes
        from ctypes import wintypes

        ulong_ptr = ctypes.c_size_t

        class MOUSEINPUT(ctypes.Structure):
            _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                        ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ulong_ptr)]

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                        ("time", wintypes.DWORD), ("dwExtraInfo", ulong_ptr)]

        class HARDWAREINPUT(ctypes.Structure):
            _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]

        class _UNION(ctypes.Union):
            _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]

        class INPUT(ctypes.Structure):
            _anonymous_ = ("u",)
            _fields_ = [("type", wintypes.DWORD), ("u", _UNION)]

        for ch in text:
            if ch == "\n":
                self.press(KEY_CODES["enter"])
                continue
            if ch == "\r":
                continue
            units = ch.encode("utf-16-le")
            for i in range(0, len(units), 2):
                code = int.from_bytes(units[i:i + 2], "little")
                for flags in (0x0004, 0x0004 | 0x0002):  # KEYEVENTF_UNICODE, + KEYUP
                    inp = INPUT(type=1, ki=KEYBDINPUT(0, code, flags, 0, 0))
                    self.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
            time.sleep(0.005)

    # ---- 鼠标 ----
    def cursor(self) -> tuple[int, int]:
        from ctypes import wintypes

        point = wintypes.POINT()
        self.user32.GetCursorPos(self.ctypes.byref(point))
        return point.x, point.y

    def move(self, x: int, y: int) -> None:
        self.user32.SetCursorPos(int(x), int(y))

    def mouse_button(self, button: str, down: bool) -> None:
        flags = {"left": (0x0002, 0x0004), "right": (0x0008, 0x0010), "middle": (0x0020, 0x0040)}[button]
        self.user32.mouse_event(flags[0] if down else flags[1], 0, 0, 0, 0)

    def scroll(self, clicks: int, horizontal: bool = False) -> None:
        self.user32.mouse_event(0x1000 if horizontal else 0x0800, 0, 0, int(clicks) * 120, 0)

    # ---- 屏幕 ----
    def grab_screen(self, all_screens: bool = False):
        try:
            from PIL import ImageGrab
        except ImportError as e:
            raise RuntimeError("看屏幕需要 Pillow:python -m pip install pillow") from e
        return ImageGrab.grab(all_screens=all_screens)

    # ---- 窗口 ----
    def windows(self) -> list[tuple[int, str]]:
        ctypes = self.ctypes
        from ctypes import wintypes

        found = []
        proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def collect(hwnd, _):
            if self.user32.IsWindowVisible(hwnd):
                length = self.user32.GetWindowTextLengthW(hwnd)
                if length:
                    buf = ctypes.create_unicode_buffer(length + 1)
                    self.user32.GetWindowTextW(hwnd, buf, length + 1)
                    if buf.value and buf.value not in ("Program Manager", "Settings", "设置", "設定"):
                        found.append((int(hwnd or 0), buf.value))
            return True

        self.user32.EnumWindows(proc(collect), 0)
        return found

    def focus(self, hwnd: int) -> None:
        if self.user32.IsIconic(hwnd):
            self.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        # 先按一下 Alt,Windows 才允许把别的窗口切到前台
        self._key(KEY_CODES["alt"], True)
        self.user32.SetForegroundWindow(hwnd)
        self._key(KEY_CODES["alt"], False)

    def lock(self) -> None:
        self.user32.LockWorkStation()

    def set_wallpaper(self, path: str) -> None:
        if not self.user32.SystemParametersInfoW(20, 0, path, 3):  # SPI_SETDESKWALLPAPER
            raise RuntimeError("设置壁纸失败")

    # ---- 文件夹和程序 ----
    def known_folder(self, name: str) -> Path:
        import winreg

        value_names = {
            "desktop": "Desktop", "documents": "Personal", "pictures": "My Pictures",
            "music": "My Music", "videos": "My Video", "downloads": "{374DE290-123F-4565-9164-39C4925E467B}",
        }
        try:
            key_path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
                value, _ = winreg.QueryValueEx(key, value_names[name])
            return Path(os.path.expandvars(value))
        except OSError:
            return Path.home() / name.capitalize()

    def steam_root(self) -> Path | None:
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
                value, _ = winreg.QueryValueEx(key, "SteamPath")
            return Path(value)
        except OSError:
            return None

    def open(self, target: str) -> None:
        os.startfile(target)  # noqa: S606

    def run(self, args: list[str]) -> tuple[int, str]:
        proc = subprocess.run(args, capture_output=True, timeout=30)
        out = (proc.stdout + proc.stderr).decode(errors="replace").strip()
        return proc.returncode, out

    def powershell(self, script: str, stdin: str | None = None, env: dict | None = None) -> tuple[int, str]:
        prefix = (
            "[Console]::OutputEncoding = [Text.Encoding]::UTF8; [Console]::InputEncoding = [Text.Encoding]::UTF8;"
            "$ProgressPreference = 'SilentlyContinue';"
        )
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", prefix + script],
            input=(stdin or "").encode("utf-8"),
            capture_output=True,
            timeout=120,
            env={**os.environ, **(env or {})},
        )
        out = proc.stdout.decode("utf-8", errors="replace").strip()
        if proc.returncode not in (0, 3):
            err = proc.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(err or f"PowerShell 退出码 {proc.returncode}")
        return proc.returncode, out

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


# ======================== Steam 游戏 ========================


def steam_library_dirs(steam_root: Path) -> list[Path]:
    dirs = [steam_root]
    try:
        text = (steam_root / "steamapps" / "libraryfolders.vdf").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return dirs
    for raw in re.findall(r'"path"\s+"([^"]+)"', text):
        path = Path(raw.replace("\\\\", "\\"))
        if path not in dirs:
            dirs.append(path)
    return dirs


def steam_games(steam_root: Path | None) -> list[tuple[str, str]]:
    """返回 [(appid, 游戏名)]。"""
    if steam_root is None:
        return []
    games = []
    for lib in steam_library_dirs(steam_root):
        for manifest in sorted((lib / "steamapps").glob("appmanifest_*.acf")):
            try:
                text = manifest.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            appid = re.search(r'"appid"\s+"(\d+)"', text)
            name = re.search(r'"name"\s+"([^"]+)"', text)
            if appid and name:
                games.append((appid.group(1), name.group(1)))
    return games


def best_match(names: list[str], query: str) -> int | None:
    """在名字列表里找最匹配 query 的一个,返回下标。"""
    q = query.lower().strip()
    if not q:
        return None
    lowered = [n.lower() for n in names]
    for check in (lambda n: n == q, lambda n: n.startswith(q), lambda n: q in n,
                  lambda n: all(word in n for word in q.split())):
        for i, n in enumerate(lowered):
            if check(n):
                return i
    return None


# ======================== 找文件 ========================

SKIP_DIRS = {"node_modules", "appdata", "$recycle.bin", "__pycache__", "venv", ".venv"}
EXECUTABLE_SUFFIXES = {".exe", ".bat", ".cmd", ".ps1", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".msi", ".com",
                       ".scr", ".lnk", ".url", ".reg", ".hta", ".jar", ".py", ".pyw"}


def find_files(roots: list[Path], keyword: str, ext: str = "", days: int = 0, limit: int = 30,
               time_budget: float = 8.0, max_depth: int = 6) -> list[Path]:
    """在 roots 里按文件名找文件,最近修改的排前面。"""
    keyword = keyword.lower().strip()
    ext = ext.lower().strip().lstrip("*")
    if ext and not ext.startswith("."):
        ext = "." + ext
    since = time.time() - days * 86400 if days else 0
    deadline = time.monotonic() + time_budget
    found: list[tuple[float, Path]] = []
    seen = set()
    for root in roots:
        root = Path(root)
        if not root.is_dir() or root.resolve() in seen:
            continue
        seen.add(root.resolve())
        base_depth = len(root.parts)
        for dirpath, dirnames, filenames in os.walk(root):
            if time.monotonic() > deadline:
                break
            depth = len(Path(dirpath).parts) - base_depth
            dirnames[:] = [d for d in dirnames if not d.startswith(".") and d.lower() not in SKIP_DIRS
                           and depth < max_depth]
            for name in filenames:
                lower = name.lower()
                if keyword and keyword not in lower:
                    continue
                if ext and not lower.endswith(ext):
                    continue
                path = Path(dirpath) / name
                try:
                    mtime = path.stat().st_mtime
                except OSError:
                    continue
                if mtime >= since:
                    found.append((mtime, path))
    found.sort(key=lambda item: item[0], reverse=True)
    return [p for _, p in found[:limit]]


def _size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n}B"


# ======================== PowerShell 脚本 ========================

FIND_AND_OPEN_APP = r"""
$n = $env:FRIDAY_APP_NAME
$apps = @(Get-StartApps | Where-Object { $_.Name -like ('*' + $n + '*') })
$exact = @($apps | Where-Object { $_.Name -eq $n })
if ($exact.Count -gt 0) { $app = $exact[0] } elseif ($apps.Count -gt 0) { $app = $apps[0] } else { exit 3 }
Start-Process ('shell:AppsFolder\' + $app.AppID)
Write-Output $app.Name
"""

SAVE_SCREENSHOT = r"""
Add-Type -AssemblyName System.Windows.Forms, System.Drawing
$b = [System.Windows.Forms.SystemInformation]::VirtualScreen
$bmp = New-Object System.Drawing.Bitmap $b.Width, $b.Height
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($b.Left, $b.Top, 0, 0, $bmp.Size)
$bmp.Save($env:FRIDAY_SHOT_PATH, [System.Drawing.Imaging.ImageFormat]::Png)
"""

SYSTEM_STATUS = r"""
$os = Get-CimInstance Win32_OperatingSystem
$cpu = (Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average
$disks = @(Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3" | ForEach-Object {
  @{ name = $_.DeviceID; free = [int64]$_.FreeSpace; size = [int64]$_.Size } })
$bat = Get-CimInstance Win32_Battery -ErrorAction SilentlyContinue | Select-Object -First 1
$top = @(Get-Process | Sort-Object WorkingSet64 -Descending | Select-Object -First 8 | ForEach-Object {
  @{ name = $_.ProcessName; mem = [int64]$_.WorkingSet64 } })
$ip = @(Get-NetIPConfiguration -ErrorAction SilentlyContinue | Where-Object { $_.IPv4DefaultGateway } |
  ForEach-Object { $_.IPv4Address.IPAddress })
$links = @(Get-NetAdapter -ErrorAction SilentlyContinue | Where-Object { $_.Status -eq 'Up' } |
  ForEach-Object { '{0} {1}' -f $_.Name, $_.LinkSpeed })
$wifi = (netsh wlan show interfaces | Select-String '^\s*SSID\s*:' | Select-Object -First 1)
@{
  cpu = $cpu; mem_total = [int64]$os.TotalVisibleMemorySize * 1024; mem_free = [int64]$os.FreePhysicalMemory * 1024;
  disks = $disks; battery = $(if ($bat) { $bat.EstimatedChargeRemaining } else { $null });
  top = $top; ip = $ip; links = $links; wifi = $(if ($wifi) { ($wifi.ToString() -split ':', 2)[1].Trim() } else { $null })
} | ConvertTo-Json -Depth 4 -Compress
"""

SET_BRIGHTNESS = r"""
$m = Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods -ErrorAction SilentlyContinue
if (-not $m) { exit 3 }
$m | Invoke-CimMethod -MethodName WmiSetBrightness -Arguments @{ Timeout = 1; Brightness = [byte]$env:FRIDAY_LEVEL } | Out-Null
"""

SET_THEME = r"""
$p = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize'
Set-ItemProperty -Path $p -Name AppsUseLightTheme -Value ([int]$env:FRIDAY_LIGHT) -Type DWord
Set-ItemProperty -Path $p -Name SystemUsesLightTheme -Value ([int]$env:FRIDAY_LIGHT) -Type DWord
"""

CLOSE_APP = r"""
$n = $env:FRIDAY_APP_NAME
$procs = @(Get-Process | Where-Object { $_.MainWindowHandle -ne 0 -and
  ($_.ProcessName -like ('*' + $n + '*') -or $_.MainWindowTitle -like ('*' + $n + '*')) })
if ($procs.Count -eq 0) { exit 3 }
foreach ($p in $procs) { $null = $p.CloseMainWindow(); Write-Output $p.ProcessName }
"""

EMPTY_RECYCLE_BIN = "Clear-RecycleBin -Force -ErrorAction SilentlyContinue"

SLEEP = r"""
Add-Type -AssemblyName System.Windows.Forms
[System.Windows.Forms.Application]::SetSuspendState('Suspend', $false, $false) | Out-Null
"""

AUDIO_HAS_MODULE = "if (Get-Module -ListAvailable -Name AudioDeviceCmdlets) { 'yes' } else { 'no' }"
AUDIO_INSTALL = (
    "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12;"
    "Install-PackageProvider -Name NuGet -MinimumVersion 2.8.5.201 -Scope CurrentUser -Force | Out-Null;"
    "Install-Module -Name AudioDeviceCmdlets -Scope CurrentUser -Force -AllowClobber"
)
AUDIO_LIST = r"""
Import-Module AudioDeviceCmdlets
Get-AudioDevice -List | Where-Object { $_.Type -eq 'Playback' } |
  ForEach-Object { '{0}|{1}' -f $(if ($_.Default) { '*' } else { ' ' }), $_.Name }
"""
AUDIO_SET = r"""
Import-Module AudioDeviceCmdlets
$n = $env:FRIDAY_AUDIO
$d = @(Get-AudioDevice -List | Where-Object { $_.Type -eq 'Playback' -and $_.Name -like ('*' + $n + '*') })
if ($d.Count -eq 0) { exit 3 }
Set-AudioDevice -ID $d[0].ID | Out-Null
Write-Output $d[0].Name
"""

FOLDER_NAMES = {
    "下载": "downloads", "downloads": "downloads", "桌面": "desktop", "desktop": "desktop",
    "文档": "documents", "documents": "documents", "图片": "pictures", "pictures": "pictures",
    "视频": "videos", "videos": "videos", "音乐": "music", "music": "music",
}


# ======================== 一句话就能做的操作 ========================

COMPUTER_ACTIONS = {
    # 声音和媒体
    "volume_up": "调高音量(value 为百分比,默认 10)",
    "volume_down": "调低音量(value 为百分比,默认 10)",
    "mute": "静音 / 取消静音",
    "play_pause": "播放 / 暂停",
    "next_track": "下一首",
    "previous_track": "上一首",
    "audio_devices": "列出所有播放设备(耳机、音箱等),* 号是当前在用的",
    "switch_audio": "切换播放设备,value 是设备名里的关键词(先用 audio_devices 看有哪些)",
    # 程序、游戏、网址、文件夹
    "open_app": "打开开始菜单里的程序,value 是程序名,如 Discord、Spotify、记事本",
    "open_game": "打开游戏,value 是游戏名(英文名更准,如 Apex Legends、Dota 2);先找 Steam,找不到再找开始菜单",
    "open_url": "用默认浏览器打开 value 里的网址",
    "open_path": "打开文件夹或文件,value 是路径或 下载/桌面/文档/图片/视频/音乐;打开程序文件需要用户确认",
    "find_files": "按文件名找文件,value 是关键词;可选 ext(如 pdf)、days(最近几天修改过)",
    # 窗口
    "list_windows": "列出打开的窗口",
    "focus_window": "切到某个窗口,value 是窗口标题里的关键词",
    "show_desktop": "最小化全部窗口 / 显示桌面",
    "close_app": "关闭程序(正常关闭,会提示保存),value 是程序名或窗口标题关键词,需要用户确认",
    # 状态
    "system_status": "查看 CPU、内存、硬盘剩余空间、电池、最占内存的程序、IP、网络",
    # 显示
    "screenshot": "截取整个屏幕,保存到“图片\\Friday”文件夹(想看屏幕内容用 screen 工具)",
    "brightness": "设置屏幕亮度,value 为 0-100(一般只有笔记本支持)",
    "dark_mode": "切换到深色模式",
    "light_mode": "切换到浅色模式",
    "set_wallpaper": "把 value 里的图片设为桌面壁纸",
    # 剪贴板
    "read_clipboard": "读取剪贴板里的文字",
    "write_clipboard": "把 value 里的文字复制到剪贴板",
    # 电源和系统
    "lock_screen": "锁定电脑屏幕",
    "shutdown": "关机,value 为几分钟后(默认 0 立刻),需要用户确认",
    "restart": "重启,value 为几分钟后(默认 0 立刻),需要用户确认",
    "cancel_shutdown": "取消已安排的关机或重启",
    "sleep": "让电脑睡眠,需要用户确认",
    "empty_recycle_bin": "清空回收站,需要用户确认",
}


class Computer:
    """“星期五”一句话就能做的电脑操作。approve 用来请用户确认有风险的操作。"""

    def __init__(self, backend=None, pictures_dir: str | os.PathLike | None = None, clock: Clock = _now) -> None:
        self.backend = backend
        self._pictures_dir = Path(pictures_dir) if pictures_dir else None
        self.clock = clock

    @property
    def available(self) -> bool:
        return self.backend is not None or os.name == "nt"

    def _pc(self):
        if self.backend is None:
            if os.name != "nt":
                raise RuntimeError("这个功能目前只支持 Windows")
            self.backend = WindowsPC()
        return self.backend

    def folder(self, name: str) -> Path:
        return self._pc().known_folder(name)

    def do(self, action: str, value: str = "", approve: Approver = _allow, ext: str = "", days: int = 0) -> str:
        if action not in COMPUTER_ACTIONS:
            raise ValueError(f"不支持的操作:{action}")
        pc = self._pc()
        value = (value or "").strip()
        handler = getattr(self, f"_do_{action}", None)
        if handler is not None:
            return handler(pc, value, approve, ext=ext, days=days)
        if action in VK:
            return self._media(pc, action, value)
        raise ValueError(f"不支持的操作:{action}")

    @staticmethod
    def _confirm(approve: Approver, text: str) -> None:
        if not approve(text):
            raise NeedsUser("用户没有允许这个操作")

    # ---- 声音 ----
    def _media(self, pc, action, value):
        if action in ("volume_up", "volume_down"):
            percent = int(value) if value.isdigit() else 10
            times = max(1, min(50, round(percent / 2)))  # 每按一次音量键变化 2%
            pc.press(VK[action], times)
            return f"已{'调高' if action == 'volume_up' else '调低'}音量约 {times * 2}%"
        pc.press(VK[action])
        return {"mute": "已切换静音", "play_pause": "已切换播放/暂停", "next_track": "已切到下一首",
                "previous_track": "已切到上一首"}[action]

    def _audio_module(self, pc, approve) -> None:
        _, has = pc.powershell(AUDIO_HAS_MODULE)
        if has.strip() == "yes":
            return
        self._confirm(approve, "第一次切换耳机/音箱,需要从微软 PowerShell Gallery 安装 AudioDeviceCmdlets 模块(只装到你的账户)")
        pc.powershell(AUDIO_INSTALL)

    def _do_audio_devices(self, pc, value, approve, **_):
        self._audio_module(pc, approve)
        _, out = pc.powershell(AUDIO_LIST)
        lines = [line.split("|", 1) for line in out.splitlines() if "|" in line]
        if not lines:
            return "没有找到播放设备"
        return "播放设备(* 是当前在用的):\n" + "\n".join(f"{mark.strip() or ' '} {name}" for mark, name in lines)

    def _do_switch_audio(self, pc, value, approve, **_):
        if not value:
            raise ValueError("请在 value 里写设备名关键词")
        self._audio_module(pc, approve)
        code, name = pc.powershell(AUDIO_SET, env={"FRIDAY_AUDIO": value})
        if code == 3 or not name:
            return f"没有找到名字包含“{value}”的播放设备,先用 audio_devices 看看有哪些"
        return f"已切换到:{name}"

    # ---- 程序、游戏、文件 ----
    def _do_open_app(self, pc, value, approve, **_):
        if not value:
            raise ValueError("请在 value 里写程序名")
        code, opened = pc.powershell(FIND_AND_OPEN_APP, env={"FRIDAY_APP_NAME": value})
        if code == 3 or not opened:
            return f"开始菜单里没有找到名字包含“{value}”的程序,可以换个叫法"
        return f"已打开 {opened}"

    def _do_open_game(self, pc, value, approve, **_):
        if not value:
            raise ValueError("请在 value 里写游戏名")
        games = steam_games(pc.steam_root())
        index = best_match([name for _, name in games], value)
        if index is not None:
            appid, name = games[index]
            pc.open(f"steam://rungameid/{appid}")
            return f"已通过 Steam 启动 {name}"
        result = self._do_open_app(pc, value, approve)
        if result.startswith("已打开"):
            return result
        steam_list = "、".join(name for _, name in games[:30]) or "(没有找到 Steam 游戏)"
        return f"没找到“{value}”。Steam 里装了:{steam_list}"

    def _do_open_url(self, pc, value, approve, **_):
        if not value.lower().startswith(("http://", "https://")):
            raise ValueError("open_url 只能打开 http:// 或 https:// 开头的网址")
        pc.open(value)
        return f"已在浏览器打开 {value}"

    def _do_open_path(self, pc, value, approve, **_):
        if not value:
            raise ValueError("请在 value 里写路径")
        known = FOLDER_NAMES.get(value.lower())
        path = pc.known_folder(known) if known else Path(os.path.expandvars(os.path.expanduser(value)))
        if not path.exists():
            return f"找不到 {path}"
        if path.is_file() and path.suffix.lower() in EXECUTABLE_SUFFIXES:
            self._confirm(approve, f"运行程序文件:{path}")
        pc.open(str(path))
        return f"已打开 {path}"

    def _do_find_files(self, pc, value, approve, ext="", days=0, **_):
        if not value and not ext:
            raise ValueError("请在 value 里写文件名关键词,或给出 ext")
        roots = [pc.known_folder(n) for n in ("desktop", "downloads", "documents", "pictures", "videos", "music")]
        roots.append(Path.home())
        files = find_files(roots, value, ext=ext, days=int(days or 0))
        if not files:
            return "没有找到符合条件的文件"
        lines = []
        for path in files:
            st = path.stat()
            when = datetime.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
            lines.append(f"{path}  ({_size(st.st_size)},{when})")
        return f"找到 {len(files)} 个文件(最近修改的在前):\n" + "\n".join(lines)

    # ---- 窗口 ----
    def _do_list_windows(self, pc, value, approve, **_):
        titles = [t for _, t in pc.windows()]
        return ("打开的窗口:\n" + "\n".join(f"- {t}" for t in titles)) if titles else "没有打开的窗口"

    def _do_focus_window(self, pc, value, approve, **_):
        windows = pc.windows()
        index = best_match([t for _, t in windows], value)
        if index is None:
            return f"没有找到标题包含“{value}”的窗口,用 list_windows 看看有哪些"
        hwnd, title = windows[index]
        pc.focus(hwnd)
        return f"已切到:{title}"

    def _do_show_desktop(self, pc, value, approve, **_):
        pc.hotkey([KEY_CODES["win"], ord("D")])
        return "已显示桌面"

    def _do_close_app(self, pc, value, approve, **_):
        if not value:
            raise ValueError("请在 value 里写程序名")
        self._confirm(approve, f"关闭程序:{value}(会正常关闭,有没保存的内容时程序会提示)")
        code, out = pc.powershell(CLOSE_APP, env={"FRIDAY_APP_NAME": value})
        if code == 3 or not out:
            return f"没有找到名字包含“{value}”的程序窗口"
        return "已关闭:" + "、".join(sorted(set(out.split())))

    # ---- 状态 ----
    def _do_system_status(self, pc, value, approve, **_):
        _, out = pc.powershell(SYSTEM_STATUS)
        data = json.loads(out)
        gb = 1024 ** 3
        used = (data["mem_total"] - data["mem_free"]) / gb
        lines = [
            f"CPU 使用率:{data.get('cpu')}%",
            f"内存:已用 {used:.1f}GB / 共 {data['mem_total'] / gb:.1f}GB",
        ]
        for d in data.get("disks") or []:
            lines.append(f"硬盘 {d['name']}:剩余 {d['free'] / gb:.0f}GB / 共 {d['size'] / gb:.0f}GB")
        if data.get("battery") is not None:
            lines.append(f"电池:{data['battery']}%")
        top = "、".join(f"{p['name']} {p['mem'] / 1024 ** 2:.0f}MB" for p in data.get("top") or [])
        lines.append(f"最占内存:{top}")
        if data.get("ip"):
            lines.append("IP:" + "、".join(data["ip"] if isinstance(data["ip"], list) else [data["ip"]]))
        if data.get("links"):
            lines.append("网络连接:" + "、".join(data["links"] if isinstance(data["links"], list) else [data["links"]]))
        if data.get("wifi"):
            lines.append(f"Wi-Fi:{data['wifi']}")
        return "\n".join(lines)

    # ---- 显示 ----
    @property
    def pictures_dir(self) -> Path:
        return self._pictures_dir or (self.folder("pictures") / "Friday")

    def _do_screenshot(self, pc, value, approve, **_):
        folder = self.pictures_dir
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"截图_{self.clock():%Y%m%d_%H%M%S}.png"
        try:
            pc.grab_screen(all_screens=True).save(path)
        except RuntimeError:  # 没装 Pillow 时用 PowerShell
            pc.powershell(SAVE_SCREENSHOT, env={"FRIDAY_SHOT_PATH": str(path)})
        return f"截图已保存到 {path}"

    def _do_brightness(self, pc, value, approve, **_):
        if not value.isdigit() or not 0 <= int(value) <= 100:
            raise ValueError("亮度要是 0-100 的数字")
        code, _ = pc.powershell(SET_BRIGHTNESS, env={"FRIDAY_LEVEL": value})
        if code == 3:
            return "这台电脑的屏幕不支持用软件调亮度(外接显示器一般要用显示器上的按钮调)"
        return f"亮度已调到 {value}%"

    def _do_dark_mode(self, pc, value, approve, **_):
        pc.powershell(SET_THEME, env={"FRIDAY_LIGHT": "0"})
        return "已切换到深色模式"

    def _do_light_mode(self, pc, value, approve, **_):
        pc.powershell(SET_THEME, env={"FRIDAY_LIGHT": "1"})
        return "已切换到浅色模式"

    def _do_set_wallpaper(self, pc, value, approve, **_):
        path = Path(os.path.expandvars(os.path.expanduser(value)))
        if not path.is_file() or path.suffix.lower() not in (".jpg", ".jpeg", ".png", ".bmp"):
            raise ValueError(f"找不到图片 {path}(支持 jpg、png、bmp)")
        pc.set_wallpaper(str(path.resolve()))
        return f"已把壁纸换成 {path.name}"

    # ---- 剪贴板 ----
    def _do_read_clipboard(self, pc, value, approve, **_):
        _, text = pc.powershell("Get-Clipboard -Raw")
        return f"剪贴板里的内容:\n{text}" if text else "剪贴板里没有文字"

    def _do_write_clipboard(self, pc, value, approve, **_):
        pc.powershell("Set-Clipboard -Value ([Console]::In.ReadToEnd())", stdin=value)
        return "已复制到剪贴板"

    # ---- 电源和系统 ----
    def _do_lock_screen(self, pc, value, approve, **_):
        pc.lock()
        return "已锁定屏幕"

    @staticmethod
    def _minutes(value: str) -> int:
        if not value:
            return 0
        if not value.isdigit():
            raise ValueError("value 要是分钟数")
        return int(value)

    def _do_shutdown(self, pc, value, approve, **_):
        minutes = self._minutes(value)
        when = f"{minutes} 分钟后" if minutes else "立刻"
        self._confirm(approve, f"{when}关机(没保存的文件会丢失)")
        pc.run(["shutdown", "/s", "/t", str(minutes * 60)])
        return f"电脑将在{when}关机。想取消就说“取消关机”"

    def _do_restart(self, pc, value, approve, **_):
        minutes = self._minutes(value)
        when = f"{minutes} 分钟后" if minutes else "立刻"
        self._confirm(approve, f"{when}重启电脑(没保存的文件会丢失)")
        pc.run(["shutdown", "/r", "/t", str(minutes * 60)])
        return f"电脑将在{when}重启。想取消就说“取消关机”"

    def _do_cancel_shutdown(self, pc, value, approve, **_):
        code, _ = pc.run(["shutdown", "/a"])
        return "已取消关机/重启" if code == 0 else "现在没有安排关机或重启"

    def _do_sleep(self, pc, value, approve, **_):
        self._confirm(approve, "让电脑进入睡眠")
        pc.powershell(SLEEP)
        return "电脑已睡眠"

    def _do_empty_recycle_bin(self, pc, value, approve, **_):
        self._confirm(approve, "清空回收站(清空后找不回来)")
        pc.powershell(EMPTY_RECYCLE_BIN)
        return "回收站已清空"


# ======================== 看屏幕、控制鼠标和键盘 ========================

MAX_SHOT_EDGE = 1366
MAX_SHOT_PIXELS = 1_150_000
TAKEOVER_DISTANCE = 8  # 鼠标被移动超过这么多像素,就认为用户接管了

CONTROL_ACTIONS = {
    "click": "点击,需要 x、y;可选 button(left/right/middle)、clicks(2 为双击)",
    "move": "把鼠标移到 x、y",
    "drag": "按住左键从 x、y 拖到 x2、y2",
    "scroll": "在 x、y 处滚动,amount 为格数,正数向下、负数向上",
    "type": "在当前光标处输入 text(支持中文,\\n 会按回车)",
    "key": "按键或组合键,keys 如 enter、ctrl+c、alt+tab、ctrl+shift+t",
    "wait": "等待 amount 秒(最多 10 秒),比如等网页加载",
}


class ScreenControl:
    """看屏幕、控制鼠标和键盘,带安全措施。"""

    MAX_ACTIONS = 60

    def __init__(self, backend=None) -> None:
        self.backend = backend
        self.scale: float | None = None
        self.screen_size = (0, 0)
        self.begin_turn()

    @property
    def available(self) -> bool:
        return self.backend is not None or os.name == "nt"

    def _pc(self):
        if self.backend is None:
            if os.name != "nt":
                raise RuntimeError("这个功能目前只支持 Windows")
            self.backend = WindowsPC()
        return self.backend

    def begin_turn(self) -> None:
        """每个新问题开始时调用:重新征求接管同意、重新计数。"""
        self.granted = False
        self.actions = 0
        self.expected_cursor: tuple[int, int] | None = None
        self.used = False

    def look(self) -> list[dict]:
        """截屏并缩小到适合模型查看的尺寸,返回 tool_result 的内容块。"""
        image = self._pc().grab_screen()
        width, height = image.size
        scale = min(1.0, MAX_SHOT_EDGE / max(width, height), (MAX_SHOT_PIXELS / (width * height)) ** 0.5)
        shot = image if scale >= 1.0 else image.resize((round(width * scale), round(height * scale)))
        buf = io.BytesIO()
        shot.convert("RGB").save(buf, format="JPEG", quality=80)
        self.scale = scale
        self.screen_size = (width, height)
        self.expected_cursor = None  # 重新看过屏幕,不再拿之前的位置比较
        return [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                         "data": base64.b64encode(buf.getvalue()).decode()}},
            {"type": "text", "text": f"这是主屏幕的截图,尺寸 {shot.size[0]}x{shot.size[1]}。"
                                     "用 control 操作时,x、y 按这张截图里的像素位置给出。"},
        ]

    def _to_screen(self, x, y) -> tuple[int, int]:
        if self.scale is None:
            raise ValueError("请先用 screen 工具看一下屏幕,再按截图里的位置操作")
        if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
            raise ValueError("需要 x 和 y 坐标")
        sx, sy = round(x / self.scale), round(y / self.scale)
        width, height = self.screen_size
        if not (0 <= sx < width and 0 <= sy < height):
            raise ValueError(f"坐标 ({x}, {y}) 超出了截图范围")
        return sx, sy

    def _check_takeover(self, pc) -> None:
        if self.expected_cursor is None:
            return
        cx, cy = pc.cursor()
        ex, ey = self.expected_cursor
        if abs(cx - ex) > TAKEOVER_DISTANCE or abs(cy - ey) > TAKEOVER_DISTANCE:
            self.granted = False
            self.expected_cursor = None
            raise NeedsUser("用户动了鼠标,已经停止接管。先问用户要不要继续,不要马上重试")

    def act(self, args: dict, approve: Approver) -> str:
        action = args.get("action")
        if action not in CONTROL_ACTIONS:
            raise ValueError(f"不支持的操作:{action}")
        pc = self._pc()
        reason = (args.get("reason") or "").strip() or "完成用户交代的事"
        keys = normalize_combo(args.get("keys") or "")

        if not self.granted:
            if not approve(f"接管鼠标和键盘:{reason}\n  (过程中你动一下鼠标,星期五就会停下)"):
                raise NeedsUser("用户没有允许接管鼠标和键盘")
            self.granted = True
        self._check_takeover(pc)
        self.actions += 1
        if self.actions > self.MAX_ACTIONS:
            raise NeedsUser(f"这个问题里已经操作了 {self.MAX_ACTIONS} 步,先停下来问用户要不要继续")
        if args.get("irreversible") or keys in DANGEROUS_COMBOS:
            if not approve(f"可能无法撤销的操作:{reason}({self._describe(args)})"):
                raise NeedsUser("用户没有允许这一步")

        self.used = True
        result = self._perform(pc, action, args, keys)
        pc.sleep(0.15)
        self.expected_cursor = pc.cursor()
        return result

    @staticmethod
    def _describe(args: dict) -> str:
        action = args.get("action")
        if action == "type":
            text = args.get("text") or ""
            return f"输入“{text[:30]}{'…' if len(text) > 30 else ''}”"
        if action == "key":
            return f"按 {args.get('keys')}"
        if "x" in args:
            return f"{CONTROL_ACTIONS[action].split(',')[0]} ({args.get('x')}, {args.get('y')})"
        return action

    def _perform(self, pc, action, args, keys) -> str:
        if action == "wait":
            seconds = min(10.0, max(0.0, float(args.get("amount") or 1)))
            pc.sleep(seconds)
            return f"等了 {seconds:g} 秒"
        if action == "type":
            text = args.get("text")
            if not isinstance(text, str) or not text:
                raise ValueError("type 需要 text")
            if len(text) > 2000:
                raise ValueError("一次最多输入 2000 个字")
            pc.type_text(text)
            return f"已输入 {len(text)} 个字"
        if action == "key":
            pc.hotkey(parse_keys(keys))
            return f"已按 {keys}"

        x, y = self._to_screen(args.get("x"), args.get("y"))
        if action == "move":
            pc.move(x, y)
            return f"鼠标已移到 ({args['x']}, {args['y']})"
        if action == "click":
            button = args.get("button") or "left"
            if button not in ("left", "right", "middle"):
                raise ValueError("button 只能是 left、right、middle")
            clicks = int(args.get("clicks") or 1)
            pc.move(x, y)
            for _ in range(max(1, min(3, clicks))):
                pc.mouse_button(button, True)
                pc.mouse_button(button, False)
                pc.sleep(0.05)
            name = {1: "单击", 2: "双击", 3: "三击"}[max(1, min(3, clicks))]
            return f"已{'右键' if button == 'right' else ''}{name} ({args['x']}, {args['y']})"
        if action == "drag":
            x2, y2 = self._to_screen(args.get("x2"), args.get("y2"))
            pc.move(x, y)
            pc.mouse_button("left", True)
            steps = 15
            for i in range(1, steps + 1):
                pc.move(round(x + (x2 - x) * i / steps), round(y + (y2 - y) * i / steps))
                pc.sleep(0.01)
            pc.mouse_button("left", False)
            return f"已从 ({args['x']}, {args['y']}) 拖到 ({args['x2']}, {args['y2']})"
        # scroll
        amount = int(args.get("amount") or 3)
        pc.move(x, y)
        pc.scroll(-amount)  # 滚轮正数是向上,这里约定正数向下
        return f"已{'向下' if amount > 0 else '向上'}滚动 {abs(amount)} 格"


def drain_console_input() -> None:
    """丢掉键盘缓冲区里的内容(鼠标键盘控制期间可能误打进了星期五自己的窗口)。"""
    if os.name != "nt":
        return
    import msvcrt

    while msvcrt.kbhit():
        msvcrt.getwch()
