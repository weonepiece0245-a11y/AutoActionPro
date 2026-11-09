"""把“星期五”装成一个 Windows 应用:专属图标、桌面和开始菜单快捷方式,可选开机自动启动。

用法(在 assistant 文件夹里):
    python friday_app.py install     安装(双击 install-app.bat 也一样)
    python friday_app.py uninstall   删除这些快捷方式
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Callable

SCRIPT_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
ICON_NAME = "friday.ico"

# (快捷方式名称, 要启动的 bat, 参数, 说明, 是否最小化启动, 是否放桌面)
SHORTCUTS = [
    ("星期五", "friday-app.bat", "", "私人 AI 助手星期五(图形界面)", True, True),
    ("星期五 命令行", "friday.bat", "", "星期五(黑色命令行窗口版)", False, False),  # 备用,只放开始菜单
]
LEGACY_SHORTCUTS = ["星期五 语音"]  # 旧版本建过的快捷方式,安装时顺手删掉
AUTOSTART_NAME = "星期五(开机启动)"

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyhbd.ttc",  # 微软雅黑 粗体
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\msjhbd.ttc",  # 微软正黑(繁体系统)
]

# 用 Windows 自带的 WScript.Shell 建快捷方式;路径和名字通过环境变量传,避免引号和中文转义问题
CREATE_SHORTCUT = r"""
$folder = [Environment]::GetFolderPath($env:FRIDAY_FOLDER)
$path = Join-Path $folder ($env:FRIDAY_NAME + '.lnk')
$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut($path)
$lnk.TargetPath = $env:FRIDAY_TARGET
$lnk.Arguments = $env:FRIDAY_ARGS
$lnk.WorkingDirectory = $env:FRIDAY_WORKDIR
$lnk.IconLocation = $env:FRIDAY_ICON + ',0'
$lnk.Description = $env:FRIDAY_DESC
$lnk.WindowStyle = [int]$env:FRIDAY_WINDOW
$lnk.Save()
Write-Output $path
"""

REMOVE_SHORTCUT = r"""
$folder = [Environment]::GetFolderPath($env:FRIDAY_FOLDER)
$path = Join-Path $folder ($env:FRIDAY_NAME + '.lnk')
if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path; Write-Output $path }
"""


def run_powershell(script: str, env: dict) -> str:
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
         "[Console]::OutputEncoding = [Text.Encoding]::UTF8;" + script],
        capture_output=True, timeout=60, env={**os.environ, **env},
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),  # 不闪黑色窗口
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", errors="replace").strip() or "PowerShell 出错")
    return proc.stdout.decode("utf-8", errors="replace").strip()


def make_icon(path: Path, fonts: list[str] | None = None) -> Path:
    """画一个圆形渐变图标,中间写“五”(没有中文字体时写 F),保存成多尺寸的 .ico。"""
    from PIL import Image, ImageDraw, ImageFont

    size = 256
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    gradient = Image.new("RGBA", (size, size))
    top, bottom = (40, 90, 220), (20, 200, 210)  # 深蓝到青色
    for y in range(size):
        t = y / (size - 1)
        color = tuple(round(a + (b - a) * t) for a, b in zip(top, bottom)) + (255,)
        ImageDraw.Draw(gradient).line([(0, y), (size, y)], fill=color)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((8, 8, size - 8, size - 8), fill=255)
    image.paste(gradient, (0, 0), mask)

    draw = ImageDraw.Draw(image)
    text, font = "F", None
    for candidate in fonts if fonts is not None else FONT_CANDIDATES:
        if os.path.exists(candidate):
            font, text = ImageFont.truetype(candidate, 150), "五"
            break
    if font is None:
        try:
            font = ImageFont.truetype("arialbd.ttf", 160)
        except OSError:
            font = ImageFont.load_default()
    left, top_, right, bottom_ = draw.textbbox((0, 0), text, font=font)
    x = (size - (right - left)) / 2 - left
    y = (size - (bottom_ - top_)) / 2 - top_
    draw.text((x, y), text, font=font, fill=(255, 255, 255, 255))

    image.save(path, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    return path


def find_pythonw() -> Path | None:
    """和当前 Python 同一个文件夹里的 pythonw.exe(不带黑色窗口的 Python)。"""
    candidate = Path(sys.executable).with_name("pythonw.exe")
    return candidate if candidate.exists() else None


def app_launcher(folder: Path, pythonw: Path | None, extra: str = "") -> tuple[Path, str, bool]:
    """“星期五”快捷方式启动什么:(程序, 参数, 是否最小化)。

    有 pythonw 就直接启动它:不经过 .bat,所以从网上下载的文件也不会在开机时弹“安全警告”。
    """
    if pythonw is not None:
        args = f'"{folder / "assistant.py"}" --persona friday --web {extra}'.strip()
        return pythonw, args, False
    return folder / "friday-app.bat", extra, True


def _shortcut_env(folder: str, name: str, target: Path, icon: Path, desc: str, minimized: bool,
                  args: str = "", workdir: Path | None = None) -> dict:
    return {
        "FRIDAY_FOLDER": folder,
        "FRIDAY_NAME": name,
        "FRIDAY_TARGET": str(target),
        "FRIDAY_ARGS": args,
        "FRIDAY_WORKDIR": str(workdir or target.parent),
        "FRIDAY_ICON": str(icon),
        "FRIDAY_DESC": desc,
        "FRIDAY_WINDOW": "7" if minimized else "1",  # 7 = 最小化启动
    }


def install(folder: Path = SCRIPT_DIR, ask: Callable[[str], str] = input,
            powershell: Callable[[str, dict], str] = run_powershell,
            icon_maker: Callable[[Path], Path] = make_icon, pythonw: Path | None | str = "auto") -> int:
    if os.name != "nt" and powershell is run_powershell:
        print("这个安装只支持 Windows。")
        return 1
    print("== 把星期五装成应用 ==")
    icon = folder / ICON_NAME
    try:
        icon_maker(icon)
        print(f"已画好图标:{icon}")
    except Exception as e:
        print(f"画图标失败({e}),快捷方式会用默认图标。")

    for legacy in LEGACY_SHORTCUTS:
        for place in ("Desktop", "Programs"):
            powershell(REMOVE_SHORTCUT, {"FRIDAY_FOLDER": place, "FRIDAY_NAME": legacy})

    if pythonw == "auto":
        pythonw = find_pythonw()
    for name, bat, args, desc, minimized, on_desktop in SHORTCUTS:
        target = folder / bat
        if not target.exists():
            print(f"找不到 {bat},跳过“{name}”")
            continue
        if bat == "friday-app.bat":
            target, args, minimized = app_launcher(folder, pythonw)
        places = (("Desktop", "桌面"), ("Programs", "开始菜单")) if on_desktop else (("Programs", "开始菜单"),)
        if not on_desktop:  # 以前的版本放过桌面,删掉
            powershell(REMOVE_SHORTCUT, {"FRIDAY_FOLDER": "Desktop", "FRIDAY_NAME": name})
        for place, label in places:
            powershell(CREATE_SHORTCUT, _shortcut_env(place, name, target, icon, desc, minimized, args, folder))
            print(f"已在{label}创建“{name}”")

    answer = ask("\n要不要开机自动启动星期五(在后台运行,不弹窗口;Discord 和提醒一直在线)?[y] 要  [n] 不要:")
    if answer.strip().lower() in ("y", "yes", "是", "要", "好"):
        target, args, minimized = app_launcher(folder, pythonw, "--no-browser")
        powershell(CREATE_SHORTCUT, _shortcut_env(
            "Startup", AUTOSTART_NAME, target, icon, "开机自动启动星期五(后台)",
            minimized=minimized, args=args, workdir=folder))
        print("已设置开机自动启动。不想要了,可以再运行一次选择卸载,或在“任务管理器 → 启动应用”里关掉。")
    else:
        powershell(REMOVE_SHORTCUT, {"FRIDAY_FOLDER": "Startup", "FRIDAY_NAME": AUTOSTART_NAME})
        print("不开机自动启动。")

    print("\n完成!桌面和开始菜单里都有“星期五”了(科技风界面),也可以在开始菜单里搜索“星期五”。")
    print("想用以前的黑色窗口版,在开始菜单里找“星期五 命令行”。")
    print("想固定到任务栏:在开始菜单里右键“星期五” → 固定到任务栏。")
    return 0


def autostart_path() -> Path | None:
    appdata = os.environ.get("APPDATA")
    if os.name != "nt" or not appdata:
        return None
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / f"{AUTOSTART_NAME}.lnk"


def autostart_enabled() -> bool | None:
    """开机自动启动有没有打开;不是 Windows 返回 None。"""
    path = autostart_path()
    return None if path is None else path.exists()


def set_autostart(on: bool, folder: Path = SCRIPT_DIR,
                  powershell: Callable[[str, dict], str] = run_powershell,
                  pythonw: Path | None | str = "auto") -> None:
    """打开或关掉开机自动启动(在后台运行,不弹窗口)。"""
    if not on:
        powershell(REMOVE_SHORTCUT, {"FRIDAY_FOLDER": "Startup", "FRIDAY_NAME": AUTOSTART_NAME})
        return
    if pythonw == "auto":
        pythonw = find_pythonw()
    target, args, minimized = app_launcher(folder, pythonw, "--no-browser")
    powershell(CREATE_SHORTCUT, _shortcut_env(
        "Startup", AUTOSTART_NAME, target, folder / ICON_NAME, "开机自动启动星期五(后台)",
        minimized=minimized, args=args, workdir=folder))


def uninstall(powershell: Callable[[str, dict], str] = run_powershell) -> int:
    removed = []
    for name in [s[0] for s in SHORTCUTS] + LEGACY_SHORTCUTS:
        for place in ("Desktop", "Programs"):
            out = powershell(REMOVE_SHORTCUT, {"FRIDAY_FOLDER": place, "FRIDAY_NAME": name})
            if out:
                removed.append(out)
    out = powershell(REMOVE_SHORTCUT, {"FRIDAY_FOLDER": "Startup", "FRIDAY_NAME": AUTOSTART_NAME})
    if out:
        removed.append(out)
    print("已删除:\n" + "\n".join(removed) if removed else "没有找到星期五的快捷方式。")
    return 0


def main(argv: list[str]) -> int:
    if argv and argv[0] == "uninstall":
        return uninstall()
    if argv and argv[0] == "install":
        return install()
    choice = input("[1] 安装(桌面、开始菜单快捷方式和图标)  [2] 卸载  请选择:").strip()
    return uninstall() if choice == "2" else install()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
