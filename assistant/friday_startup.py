"""开机变慢优化:列出开机自动启动的程序、哪些拖慢开机、哪些可以关;只关你同意的,随时能恢复。
顺便看:Windows 更新是不是等着重启、很久没更新,硬盘健康,临时文件多大。每 30 天自动检查一次。

关的方法和“任务管理器 → 启动应用 → 停用”一模一样(只是标记成不启动,不删除、不卸载),
随时可以说“恢复开机启动 Steam”,或在任务管理器里再打开。
装给所有用户的程序(HKLM)要管理员权限才能关,星期五会告诉你去任务管理器关。
"""

from __future__ import annotations

import datetime
import json
import os
import re
import struct
import subprocess
import time
from pathlib import Path
from typing import Callable

RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN32 = r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run"
APPROVED = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved"
CHECK_DAYS = 30
TEMP_DAYS = 7  # 临时文件超过 7 天没动才清
UPDATE_OLD_DAYS = 45

# 名字或命令里有这些:建议保留(安全软件、驱动、键鼠灯光设定、星期五自己)
KEEP = ["securityhealth", "windows security", "defender", "realtek", "rtkaud", "nvidia", "nvcpl", "amd", "radeon", "intel",
        "igfx", "synaptics", "elan", "touchpad", "logitech", "lghub", "razer", "synapse", "corsair", "icue", "steelseries",
        "bluetooth", "dolby", "nahimic", "waves", "armoury", "asus", "msi", "lenovo", "dell", "hp ", "kaspersky", "avast",
        "avg", "norton", "mcafee", "eset", "bitdefender", "malwarebytes", "friday", "星期五", "tailscale", "wireguard", "openvpn"]
# 建议关:开机不用马上开,要用时再开(游戏平台、聊天、音乐、更新器……)
OFF = {
    "steam": "游戏平台", "discord": "聊天", "epicgames": "游戏平台", "epic games": "游戏平台", "eaapp": "游戏平台",
    "ea app": "游戏平台", "origin": "游戏平台", "ubisoft": "游戏平台", "uplay": "游戏平台", "battle.net": "游戏平台",
    "riotclient": "游戏平台", "riot client": "游戏平台", "goggalaxy": "游戏平台", "spotify": "音乐", "teams": "开会软件",
    "skype": "聊天", "zoom": "开会软件", "microsoftedgeautolaunch": "浏览器预先打开", "msedge": "浏览器预先打开",
    "ccleaner": "清理软件", "itunes": "苹果软件", "adobe": "Adobe 更新器", "acrobat": "Adobe 更新器", "wechat": "聊天",
    "weixin": "聊天", "whatsapp": "聊天", "telegram": "聊天", "line": "聊天", "messenger": "聊天", "qq": "聊天",
    "opera": "浏览器", "brave": "浏览器", "utorrent": "下载软件", "qbittorrent": "下载软件", "wallpaper": "动态桌布",
    "overwolf": "游戏插件", "medal": "录影软件", "outplayed": "录影软件", "googledrive": "云端硬盘", "dropbox": "云端硬盘",
    "cortana": "微软助理", "phonelink": "手机连接", "yourphone": "手机连接", "copilot": "微软助理",
}
HEAVY = ("steam", "epic", "eaapp", "ea app", "origin", "ubisoft", "battle.net", "riot", "teams", "adobe", "overwolf", "wallpaper",
         "googledrive", "dropbox", "zoom")

REPORT = re.compile(r"^(星期五)?[,，\s]*(电脑)?(开机)(好|很|有点|越来越|变)?(慢|卡)(了|啊|怎么办)?[?？]?$"
                    r"|^(星期五)?[,，\s]*(检查|看看|看一下|优化)(一下)?(电脑的?)?(开机|开机启动|开机速度|自动启动)(的程序)?(有哪些)?[?？]?$"
                    r"|^(星期五)?[,，\s]*(开机启动|开机自动启动|自动启动)(的程序)?(有哪些|有什么)[?？]?$")
DISABLE = re.compile(r"^(星期五)?[,，\s]*(把)?(关掉|关闭|停用|不要|别)(开机启动的?|开机自动启动的?|开机自动开|开机自动打开|开机时?自己开)?"
                     r"(?P<n>[A-Za-z0-9一-鿿 .+\-]{2,30}?)(的)?(开机启动|开机自动启动|开机自动开|开机时?自己开)?[。!！]?$"
                     r"|^(星期五)?[,，\s]*开机(时|的时候)?(不要|别|不用)(再)?(自动)?(开|打开|启动|开启)(?P<n3>[A-Za-z0-9一-鿿 .+\-]{2,30}?)(了)?[。!！]?$")
ENABLE = re.compile(r"^(星期五)?[,，\s]*(恢复|打开|开回|重新打开)(开机启动的?|开机自动启动的?)(?P<n>[A-Za-z0-9一-鿿 .+\-]{2,30}?)[。!！]?$"
                    r"|^(星期五)?[,，\s]*(让)?(?P<n2>[A-Za-z0-9一-鿿 .+\-]{2,30}?)(恢复|重新)?开机(自动)?启动[。!！]?$")
CLEAN_TEMP = re.compile(r"^(星期五)?[,，\s]*(帮我)?(清理|清掉|删掉|清除)(一下)?(电脑的?)?(临时文件|暂存文件|temp|垃圾文件)[。!！]?$", re.I)


def program_name(command: str) -> str:
    """命令里的程序文件名:"C:\\a b\\Steam.exe" -silent -> Steam.exe。"""
    found = re.match(r'\s*"([^"]+)"|\s*(\S+)', str(command or ""))
    return Path((found.group(1) or found.group(2)) if found else "").name.replace("\\", "/").rsplit("/", 1)[-1]


def suggestion(name: str, command: str) -> tuple[str, str]:
    """(keep / off / maybe, 为什么)。"""
    text = f"{name} {command}".lower()
    if any(word in text for word in KEEP):
        return "keep", "驱动、安全软件或键鼠设定,建议保留"
    for word, kind in OFF.items():
        if word in text:
            if kind == "云端硬盘":
                return "maybe", "云端同步:关了就不会自动同步,看你需不需要"
            return "off", f"{kind}:开机不用马上开,要用时再打开就好"
    if "onedrive" in text:
        return "maybe", "OneDrive 同步:关了就不会自动同步,看你需不需要"
    return "maybe", "不认识的程序,不确定的话先留着"


def disabled_flag(data: bytes | None) -> bool:
    """StartupApproved 里的值:第一个字节是单数(03、07……)就是停用了。"""
    return bool(data) and data[0] & 1 == 1


def approval_bytes(enabled: bool, now: float | None = None) -> bytes:
    if enabled:
        return bytes([2] + [0] * 11)
    filetime = int(((now if now is not None else time.time()) + 11644473600) * 10_000_000)
    return bytes([3, 0, 0, 0]) + struct.pack("<Q", filetime)


class WindowsRegistry:
    def __init__(self) -> None:
        import winreg

        self.winreg = winreg
        self.hives = {"HKCU": winreg.HKEY_CURRENT_USER, "HKLM": winreg.HKEY_LOCAL_MACHINE}

    def values(self, hive: str, path: str) -> list[tuple[str, object]]:
        winreg = self.winreg
        try:
            key = winreg.OpenKey(self.hives[hive], path)
        except OSError:
            return []
        found = []
        with key:
            i = 0
            while True:
                try:
                    name, value, _ = winreg.EnumValue(key, i)
                except OSError:
                    break
                found.append((name, value))
                i += 1
        return found

    def set_binary(self, hive: str, path: str, name: str, data: bytes) -> None:
        winreg = self.winreg
        key = winreg.CreateKeyEx(self.hives[hive], path, 0, winreg.KEY_SET_VALUE)
        with key:
            winreg.SetValueEx(key, name, 0, winreg.REG_BINARY, data)


def startup_folders() -> list[tuple[str, Path]]:
    found = []
    appdata, programdata = os.environ.get("APPDATA"), os.environ.get("PROGRAMDATA")
    if appdata:
        found.append(("HKCU", Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"))
    if programdata:
        found.append(("HKLM", Path(programdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "StartUp"))
    return found


def _powershell(script: str, timeout: int = 40) -> str:
    proc = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
                           "[Console]::OutputEncoding = [Text.Encoding]::UTF8;" + script],
                          capture_output=True, timeout=timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return proc.stdout.decode("utf-8", errors="replace").strip()


def boot_info() -> dict:
    """上次开机花了多久、哪些程序拖慢了开机(Windows 的开机效能记录;没权限读就空的)。"""
    script = (
        "$ErrorActionPreference='SilentlyContinue';"
        "$b = Get-WinEvent -FilterHashtable @{LogName='Microsoft-Windows-Diagnostics-Performance/Operational';Id=100} -MaxEvents 1;"
        "$s = Get-WinEvent -FilterHashtable @{LogName='Microsoft-Windows-Diagnostics-Performance/Operational';Id=101} -MaxEvents 40;"
        "$slow = @($s | ForEach-Object { $x=[xml]$_.ToXml(); @{name=($x.Event.EventData.Data | ? Name -eq 'FriendlyName').'#text';"
        "file=($x.Event.EventData.Data | ? Name -eq 'Name').'#text'; ms=[int](($x.Event.EventData.Data | ? Name -eq 'TotalTime').'#text')} });"
        "$boot = if ($b) { [int](([xml]$b.ToXml()).Event.EventData.Data | ? Name -eq 'BootTime').'#text' } else { 0 };"
        "@{boot_ms=$boot; slow=$slow} | ConvertTo-Json -Depth 4 -Compress")
    try:
        data = json.loads(_powershell(script) or "{}")
    except (ValueError, OSError, subprocess.SubprocessError):
        return {}
    slow = data.get("slow") or []
    if isinstance(slow, dict):
        slow = [slow]
    return {"boot_seconds": round((data.get("boot_ms") or 0) / 1000), "slow": [s for s in slow if isinstance(s, dict)]}


def system_checks() -> dict:
    """Windows 更新(要不要重启、上次装更新多久了)、硬盘健康。"""
    script = (
        "$ErrorActionPreference='SilentlyContinue';"
        "$reboot = (Test-Path 'HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\WindowsUpdate\\Auto Update\\RebootRequired') -or "
        "(Test-Path 'HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Component Based Servicing\\RebootPending');"
        "$hf = Get-HotFix | Where-Object InstalledOn | Sort-Object InstalledOn -Descending | Select-Object -First 1;"
        "$disks = @(Get-PhysicalDisk | ForEach-Object { @{name=$_.FriendlyName; type=[string]$_.MediaType; health=[string]$_.HealthStatus} });"
        "@{reboot=[bool]$reboot; last_update=$(if ($hf) { $hf.InstalledOn.ToString('yyyy-MM-dd') } else { '' }); disks=$disks}"
        " | ConvertTo-Json -Depth 4 -Compress")
    try:
        data = json.loads(_powershell(script) or "{}")
    except (ValueError, OSError, subprocess.SubprocessError):
        return {}
    disks = data.get("disks") or []
    if isinstance(disks, dict):
        disks = [disks]
    return {"reboot": bool(data.get("reboot")), "last_update": str(data.get("last_update") or ""), "disks": disks}


def temp_folder() -> Path | None:
    temp = os.environ.get("TEMP") or os.environ.get("TMP")
    return Path(temp) if temp else None


def temp_size(folder: Path | None, older_days: float = 0, now: float | None = None) -> tuple[int, int]:
    """(多少字节, 几个文件);older_days:只算这么多天没动过的。"""
    if folder is None or not folder.is_dir():
        return 0, 0
    now = now if now is not None else time.time()
    total = count = 0
    for current, dirs, files in os.walk(folder):
        for name in files:
            try:
                stat = (Path(current) / name).stat()
            except OSError:
                continue
            if older_days and now - stat.st_mtime < older_days * 86400:
                continue
            total += stat.st_size
            count += 1
    return total, count


def clean_temp(folder: Path | None, older_days: float = TEMP_DAYS, now: float | None = None) -> tuple[int, int]:
    """删掉临时文件夹里很久没动的文件(正在用的删不掉就跳过)。返回(清了多少字节, 几个文件)。"""
    if folder is None or not folder.is_dir():
        return 0, 0
    now = now if now is not None else time.time()
    freed = count = 0
    for current, dirs, files in os.walk(folder, topdown=False):
        for name in files:
            path = Path(current) / name
            try:
                stat = path.lstat()
                if now - stat.st_mtime < older_days * 86400 or path.is_symlink():
                    continue
                path.unlink()
                freed += stat.st_size
                count += 1
            except OSError:
                continue
        if Path(current) != folder:
            try:
                Path(current).rmdir()  # 空的子文件夹顺便拿掉
            except OSError:
                pass
    return freed, count


def gb(size: int) -> str:
    return f"{size / 1024 ** 3:.1f} GB" if size >= 1024 ** 3 else f"{size / 1024 ** 2:.0f} MB"


class StartupDoctor:
    def __init__(self, path: str | os.PathLike, registry=None, folders: Callable[[], list] = startup_folders,
                 boot: Callable[[], dict] = boot_info, checks: Callable[[], dict] = system_checks,
                 temp: Callable[[], Path | None] = temp_folder, today: Callable[[], datetime.date] = datetime.date.today,
                 clock: Callable[[], float] = time.time) -> None:
        self.path = Path(path)
        self.registry = registry
        self.folders, self.boot, self.checks, self.temp = folders, boot, checks, temp
        self.today, self.clock = today, clock
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        self.state: dict = data if isinstance(data, dict) else {}

    def _save(self) -> None:
        temp = self.path.with_name(self.path.name + ".tmp")
        temp.write_text(json.dumps(self.state, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(temp, self.path)

    # ---------- 开机启动的程序 ----------
    def items(self) -> list[dict]:
        reg = self.registry
        if reg is None:
            return []
        found = []
        for hive, path, kind in (("HKCU", RUN, "Run"), ("HKLM", RUN, "Run"), ("HKLM", RUN32, "Run32")):
            approved = dict(reg.values(hive, f"{APPROVED}\\{kind}"))
            for name, command in reg.values(hive, path):
                if not isinstance(command, str):
                    continue
                found.append({"name": name, "command": command[:300], "hive": hive, "kind": kind,
                              "enabled": not disabled_flag(approved.get(name) if isinstance(approved.get(name), bytes) else None)})
        for hive, folder in self.folders():
            approved = dict(reg.values(hive, f"{APPROVED}\\StartupFolder"))
            try:
                files = sorted(p for p in Path(folder).iterdir() if p.is_file() and p.name.lower() != "desktop.ini")
            except OSError:
                continue
            for file in files:
                value = approved.get(file.name)
                found.append({"name": file.stem, "command": str(file), "hive": hive, "kind": "StartupFolder",
                              "value": file.name, "enabled": not disabled_flag(value if isinstance(value, bytes) else None)})
        for i, item in enumerate(found, 1):
            item["id"] = i
            item["can_toggle"] = item["hive"] == "HKCU"
            item["suggest"], item["why"] = suggestion(item["name"], item["command"])
            item["heavy"] = any(w in f"{item['name']} {item['command']}".lower() for w in HEAVY)
        return found

    def _match(self, name: str) -> list[dict]:
        key = name.strip().lower().replace(" ", "")
        if len(key) < 2:
            return []
        items = self.items()
        exact = [i for i in items if i["name"].lower().replace(" ", "") == key]
        return exact or [i for i in items if key in f"{i['name']}{program_name(i['command'])}".lower().replace(" ", "")]

    def set_enabled(self, name: str, enabled: bool) -> str:
        matches = self._match(name)
        if not matches:
            raise LookupError(f"开机启动的程序里没有“{name}”")
        if len(matches) > 1:
            names = "、".join(m["name"] for m in matches[:5])
            raise ValueError(f"有好几个对上“{name}”:{names}。说清楚一点是哪一个")
        item = matches[0]
        if item["enabled"] == enabled:
            return f"{item['name']} 本来就{'会' if enabled else '不会'}开机启动。"
        if not item["can_toggle"]:
            return (f"{item['name']} 是装给所有用户的,要管理员权限才能改:打开任务管理器(Ctrl+Shift+Esc)→ 启动应用 → "
                    f"右键 {item['name']} → {'启用' if enabled else '停用'}。")
        value = item.get("value") or item["name"]
        self.registry.set_binary("HKCU", f"{APPROVED}\\{item['kind']}", value, approval_bytes(enabled, self.clock()))
        if enabled:
            return f"好,{item['name']} 以后开机会自动启动。"
        return f"好,{item['name']} 以后开机不会自动启动了(没有删掉,要用时照常打开;说“恢复开机启动 {item['name']}”就改回来)。"

    # ---------- 报告 ----------
    def report(self) -> dict:
        items = self.items()
        boot = self.boot() or {}
        slow = {}
        for s in boot.get("slow") or []:
            key = str(s.get("file") or s.get("name") or "").lower()
            if key:
                slow[key] = max(slow.get(key, 0), int(s.get("ms") or 0))
        for item in items:
            text = f"{item['name']} {item['command']}".lower()
            hit = [ms for key, ms in slow.items() if key and (key in text or Path(key).stem in text)]
            item["slow_seconds"] = round(max(hit) / 1000, 1) if hit else 0
        checks = self.checks() or {}
        temp_bytes, temp_files = temp_size(self.temp(), TEMP_DAYS, self.clock())
        data = {"time": datetime.datetime.fromtimestamp(self.clock()).strftime("%Y-%m-%d %H:%M"), "items": items,
                "boot_seconds": boot.get("boot_seconds") or 0, "reboot": checks.get("reboot", False),
                "last_update": checks.get("last_update", ""), "disks": checks.get("disks") or [],
                "temp_bytes": temp_bytes, "temp_files": temp_files}
        self.state = {"last": data["time"][:10], "report": data}
        self._save()
        return data

    def describe(self, data: dict | None = None) -> str:
        data = data or self.report()
        items = data["items"]
        on = [i for i in items if i["enabled"]]
        lines = []
        if data.get("boot_seconds"):
            lines.append(f"上次开机花了大约 {data['boot_seconds']} 秒。")
        lines.append(f"开机会自动启动 {len(on)} 个程序" + (f"(另外 {len(items) - len(on)} 个已经关了)" if len(items) > len(on) else "") + "。")
        suggest = sorted([i for i in on if i["suggest"] == "off"], key=lambda i: (-i.get("slow_seconds", 0), not i["heavy"]))
        if suggest:
            lines.append("建议关掉(开机不用马上开):")
            for i in suggest[:10]:
                extra = f",拖慢开机约 {i['slow_seconds']} 秒" if i.get("slow_seconds") else (",比较吃资源" if i["heavy"] else "")
                admin = "(要在任务管理器关)" if not i["can_toggle"] else ""
                lines.append(f"- {i['name']}:{i['why'].split(':')[0]}{extra}{admin}")
            lines.append("要关哪个直接说,例如“关掉开机启动的 " + suggest[0]["name"] + "”,或者在 齿轮设定 → 电脑 → 开机变慢优化 里点。")
        else:
            lines.append("没有明显该关的,开机启动的都是驱动、安全软件这类。")
        slow_keep = [i for i in on if i["suggest"] != "off" and i.get("slow_seconds", 0) >= 3]
        if slow_keep:
            lines.append("这些也拖慢开机,但可能需要:" + "、".join(f"{i['name']}({i['slow_seconds']} 秒)" for i in slow_keep[:5]) + "。")
        if data.get("reboot"):
            lines.append("Windows 有更新装好了,等着重启才完成:有空重启一次,开机会比较顺。")
        last = data.get("last_update")
        if last:
            try:
                days = (self.today() - datetime.date.fromisoformat(last)).days
            except ValueError:
                days = 0
            if days >= UPDATE_OLD_DAYS:
                lines.append(f"上次装 Windows 更新是 {last}({days} 天前),可能卡住了:去 设置 → Windows 更新 看一下。")
        bad = [d for d in data.get("disks") or [] if str(d.get("health", "")).lower() not in ("healthy", "", "0")]
        if bad:
            lines.append("⚠ 硬盘健康有问题:" + "、".join(f"{d.get('name')}({d.get('health')})" for d in bad)
                         + "。重要的文件先备份!")
        elif data.get("disks"):
            lines.append("硬盘健康正常。")
        if data.get("temp_bytes", 0) >= 300 * 1024 ** 2:
            lines.append(f"临时文件夹里有 {gb(data['temp_bytes'])} 超过 {TEMP_DAYS} 天没用的临时文件,说“清理临时文件”就清掉。")
        return "\n".join(lines)

    def monthly_due(self) -> bool:
        last = self.state.get("last")
        if not last:
            return True
        try:
            return (self.today() - datetime.date.fromisoformat(last)).days >= CHECK_DAYS
        except ValueError:
            return True

    def monthly(self) -> str | None:
        """每 30 天:有该处理的才说。"""
        data = self.report()
        worth = (len([i for i in data["items"] if i["enabled"] and i["suggest"] == "off"]) >= 3 or data.get("reboot")
                 or data.get("temp_bytes", 0) >= 2 * 1024 ** 3
                 or any(str(d.get("health", "")).lower() not in ("healthy", "", "0") for d in data.get("disks") or []))
        return "每月电脑开机检查:\n" + self.describe(data) if worth else None

    def clean(self) -> str:
        freed, count = clean_temp(self.temp(), TEMP_DAYS, self.clock())
        if not count:
            return f"临时文件夹里没有超过 {TEMP_DAYS} 天的临时文件,不用清。"
        return f"清掉了 {count} 个临时文件,空出 {gb(freed)}(只清超过 {TEMP_DAYS} 天没用的,正在用的不会动)。"

    def last_report(self) -> dict | None:
        return self.state.get("report")


class Commands:
    def __init__(self, doctor: StartupDoctor) -> None:
        self.doctor = doctor

    def handle(self, text: str) -> str | None:
        text = str(text or "").strip()
        if REPORT.match(text):
            return self.doctor.describe()
        if CLEAN_TEMP.match(text):
            return self.doctor.clean()
        for pattern, enabled in ((ENABLE, True), (DISABLE, False)):
            found = pattern.match(text)
            if found and ("开机" in text or "自动启动" in text):
                groups = found.groupdict()
                name = (groups.get("n") or groups.get("n2") or groups.get("n3") or "").strip()
                try:
                    return self.doctor.set_enabled(name, enabled)
                except LookupError:
                    return None  # 不是开机启动里的:交给 Claude
                except ValueError as e:
                    return str(e)
                except OSError as e:
                    return f"没改成:{e}"
        return None
