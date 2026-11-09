"""让星期五自己更新:从 GitHub 下载最新代码,覆盖到安装文件夹里。

用 Python 下载和解压的文件不会带“来自网络”的标记,所以更新后开机也不会弹“安全警告”,
也不用再自己去浏览器下载 zip、解压、替换。

你的私人文件(.env、记忆、提醒、Google 登录、Discord 设置等)不在下载包里,也绝不会被覆盖。
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from typing import Callable

REPO = "weonepiece0245-a11y/AutoActionPro"
BRANCHES = ["claude/zealous-thompson-eoxylu", "main"]  # 先用开发分支;分支删掉后(合并了)用 main
ARCHIVE_URL = "https://github.com/{repo}/archive/refs/heads/{branch}.zip"
VERSION_FILE = "friday_version.json"
# 这些文件名永远不覆盖(都是你的私人数据或设置)
PROTECTED = {
    ".env", "credentials.json", "google_token.json", "friday_discord.json", "friday_memory.json",
    "friday_reminders.json", "friday_todos.json", "friday_web.json", "friday.log", "friday_usage.json", "friday_router.json",
    "friday_chat.json", "friday_conversation.json", "friday_archive.jsonl", VERSION_FILE,
}
MAX_DOWNLOAD = 50 * 1024 * 1024

Fetch = Callable[[str], bytes]


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "friday-updater"})
    with urllib.request.urlopen(request, timeout=60) as response:
        data = response.read(MAX_DOWNLOAD + 1)
    if len(data) > MAX_DOWNLOAD:
        raise RuntimeError("下载的文件太大,不像是星期五的更新包")
    return data


def download(fetcher: Fetch = fetch) -> tuple[str, bytes]:
    """返回 (分支, zip 内容)。开发分支不存在了就换 main。"""
    last_error: Exception | None = None
    for branch in BRANCHES:
        try:
            return branch, fetcher(ARCHIVE_URL.format(repo=REPO, branch=branch))
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise
            last_error = e
    raise RuntimeError(f"GitHub 上找不到星期五的代码({last_error})")


def commit_of(data: bytes) -> str:
    """GitHub 打包的 zip 注释里就是提交号。"""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return archive.comment.decode("ascii", errors="ignore").strip()


def _target(name: str, root: Path) -> Path | None:
    """zip 里的路径(去掉最外层文件夹)对应到本地;不安全或受保护的返回 None。"""
    parts = PurePosixPath(name.replace("\\", "/")).parts[1:]
    if not parts or any(p in ("", ".", "..") or ":" in p for p in parts):
        return None
    if parts[-1] in PROTECTED:
        return None
    target = root.joinpath(*parts)
    try:
        target.resolve().relative_to(root.resolve())
    except ValueError:
        return None
    return target


def apply(data: bytes, root: Path) -> list[str]:
    """把 zip 解到 root(仓库根目录);只写有变化的文件,返回改了哪些。"""
    changed = []
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            target = _target(info.filename, root)
            if target is None:
                continue
            content = archive.read(info)
            try:
                if target.read_bytes() == content:
                    continue
            except OSError:
                pass
            target.parent.mkdir(parents=True, exist_ok=True)
            temp = target.with_name(target.name + ".friday-update")
            temp.write_bytes(content)
            os.replace(temp, target)
            changed.append(target.relative_to(root).as_posix())
    return changed


def current_version(assistant_dir: Path) -> str:
    try:
        return json.loads((assistant_dir / VERSION_FILE).read_text(encoding="utf-8")).get("commit", "")
    except (OSError, ValueError):
        return ""


def install_requirements(assistant_dir: Path, run=subprocess.run) -> bool:
    """依赖有变化时安装(不弹黑色窗口)。"""
    python = Path(sys.executable)
    if python.name.lower() == "pythonw.exe" and python.with_name("python.exe").exists():
        python = python.with_name("python.exe")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = run([str(python), "-m", "pip", "install", "-q", "--disable-pip-version-check", "-r",
                str(assistant_dir / "requirements.txt")], capture_output=True, timeout=900, creationflags=flags)
    return proc.returncode == 0


def update(assistant_dir: Path, fetcher: Fetch = fetch, installer=install_requirements) -> dict:
    """检查并安装更新。返回 {"status": "latest" | "updated", "commit": ..., "changed": [...]}。"""
    assistant_dir = Path(assistant_dir)
    branch, data = download(fetcher)
    commit = commit_of(data)
    root = assistant_dir.parent
    changed = apply(data, root)
    if not changed and commit and commit == current_version(assistant_dir):
        return {"status": "latest", "commit": commit, "changed": [], "branch": branch}
    deps_ok = True
    if any(name.endswith("requirements.txt") for name in changed):
        deps_ok = installer(assistant_dir)
    (assistant_dir / VERSION_FILE).write_text(json.dumps({"commit": commit, "branch": branch}), encoding="utf-8")
    return {"status": "updated" if changed else "latest", "commit": commit, "changed": changed,
            "branch": branch, "deps_ok": deps_ok}


def main() -> int:
    """命令行:python friday_update.py"""
    here = Path(os.path.dirname(os.path.abspath(__file__)))
    print("正在从 GitHub 下载最新的星期五……")
    try:
        result = update(here)
    except Exception as e:
        print(f"更新失败:{e}")
        return 1
    if result["status"] == "latest":
        print("已经是最新版本了。")
    else:
        print(f"更新完成,改了 {len(result['changed'])} 个文件。请重新打开星期五。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
