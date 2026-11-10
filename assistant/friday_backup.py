"""把星期五的资料备份到 Google Drive(“星期五备份”文件夹),换电脑或重装系统后可以一键还原。

备份的是:记忆、提醒、待办、聊天记录和档案、模型选择、花费记录、Discord 配对。
不备份密码和钥匙(.env 里的 API Key、Discord Token、Admin Key,以及 Google 登录文件),
这些在新电脑上重新填一次就好,不放到网上更安全。

还原时先把下载的备份放在一边,星期五重启时才替换文件,避免运行中的星期五又把旧资料写回去。
"""

from __future__ import annotations

import datetime
import io
import json
import os
import threading
import time
import zipfile
from pathlib import Path
from typing import Callable

BACKUP_FILES = [
    "friday_memory.json", "friday_reminders.json", "friday_todos.json", "friday_archive.jsonl",
    "friday_conversation.json", "friday_chat.json", "friday_router.json", "friday_usage.json",
    "friday_discord.json", "friday_ledger.json", "friday_watch.json", "friday_mailwatch.json",
]
FOLDER_NAME = "星期五备份"
FOLDER_MIME = "application/vnd.google-apps.folder"
KEEP = 8  # Drive 里最多留几份(大约两个月)
INTERVAL_DAYS = 7
STATE_FILE = "friday_backup.json"
PENDING_FILE = "friday_restore_pending.zip"
BEFORE_RESTORE = "friday_before_restore.zip"


def make_zip(folder: Path, names: list[str] = BACKUP_FILES, when: float | None = None) -> tuple[bytes, list[str]]:
    buffer = io.BytesIO()
    included = []
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in names:
            path = folder / name
            if path.is_file():
                archive.write(path, name)
                included.append(name)
        archive.writestr("manifest.json", json.dumps({
            "created": when if when is not None else time.time(), "files": included}, ensure_ascii=False))
    return buffer.getvalue(), included


def extract_backup(data: bytes, folder: Path) -> list[str]:
    """只解出备份清单里的文件(名字对得上才写),返回解出了哪些。"""
    restored = []
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for info in archive.infolist():
            if info.filename not in BACKUP_FILES:  # 不认识的、带路径的,一律跳过
                continue
            target = folder / info.filename
            temp = target.with_name(target.name + ".restore")
            temp.write_bytes(archive.read(info))
            os.replace(temp, target)
            restored.append(info.filename)
    return restored


def apply_pending_restore(folder: Path) -> list[str]:
    """星期五启动时调用:有等待还原的备份,就先把现在的资料存一份到本机,再替换。"""
    pending = folder / PENDING_FILE
    if not pending.exists():
        return []
    data = pending.read_bytes()
    try:
        zipfile.ZipFile(io.BytesIO(data)).testzip()
    except zipfile.BadZipFile:
        pending.unlink(missing_ok=True)
        return []
    current, _ = make_zip(folder)
    (folder / BEFORE_RESTORE).write_bytes(current)
    restored = extract_backup(data, folder)
    pending.unlink(missing_ok=True)
    return restored


class DriveBackup:
    def __init__(self, folder: Path, files: Callable[[], object], clock: Callable[[], float] = time.time) -> None:
        self.folder = Path(folder)
        self.files = files  # 返回 Drive 的 files() 资源(用的时候才连 Google)
        self.clock = clock
        self.lock = threading.Lock()

    # ---------- 状态 ----------
    def _state(self) -> dict:
        try:
            return json.loads((self.folder / STATE_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def last_backup(self) -> float | None:
        value = self._state().get("last")
        return value if isinstance(value, (int, float)) else None

    def due(self) -> bool:
        last = self.last_backup()
        return last is None or self.clock() - last >= INTERVAL_DAYS * 86400

    # ---------- Drive ----------
    def _folder_id(self, files) -> str:
        query = f"name = '{FOLDER_NAME}' and mimeType = '{FOLDER_MIME}' and trashed = false"
        found = files.list(q=query, pageSize=1, fields="files(id)").execute().get("files", [])
        if found:
            return found[0]["id"]
        return files.create(body={"name": FOLDER_NAME, "mimeType": FOLDER_MIME}, fields="id").execute()["id"]

    def list_backups(self, files=None) -> list[dict]:
        files = files or self.files()
        folder_id = self._folder_id(files)
        result = files.list(q=f"'{folder_id}' in parents and trashed = false", orderBy="createdTime desc",
                            pageSize=50, fields="files(id,name,createdTime,size)").execute()
        return result.get("files", [])

    def backup(self) -> dict:
        from googleapiclient.http import MediaIoBaseUpload

        with self.lock:
            now = self.clock()
            data, included = make_zip(self.folder, when=now)
            files = self.files()
            folder_id = self._folder_id(files)
            name = datetime.datetime.fromtimestamp(now).strftime("friday-backup-%Y-%m-%d-%H%M.zip")
            media = MediaIoBaseUpload(io.BytesIO(data), mimetype="application/zip", resumable=False)
            files.create(body={"name": name, "parents": [folder_id]}, media_body=media, fields="id").execute()
            for old in self.list_backups(files)[KEEP:]:  # 只留最近几份
                files.delete(fileId=old["id"]).execute()
            (self.folder / STATE_FILE).write_text(json.dumps({"last": now, "name": name}), encoding="utf-8")
            return {"name": name, "files": included, "size": len(data)}

    def download_latest(self) -> dict:
        """把 Drive 里最新的备份下载下来,等星期五重启时再替换。"""
        files = self.files()
        backups = self.list_backups(files)
        if not backups:
            raise RuntimeError("Google Drive 的“星期五备份”文件夹里还没有备份")
        latest = backups[0]
        data = files.get_media(fileId=latest["id"]).execute()
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = [n for n in archive.namelist() if n in BACKUP_FILES]
        if not names:
            raise RuntimeError(f"“{latest['name']}”里没有星期五的资料")
        (self.folder / PENDING_FILE).write_bytes(data)
        return {"name": latest["name"], "files": names}


class BackupScheduler:
    """每小时看一次:离上次备份满 7 天,而且 Google 能用,就自动备份。"""

    def __init__(self, backup: DriveBackup, ready: Callable[[], bool], enabled: Callable[[], bool],
                 report: Callable[[str, bool], None], interval: float = 3600, first_delay: float = 120) -> None:
        self.backup = backup
        self.ready = ready
        self.enabled = enabled
        self.report = report  # (文字, 是否出错)
        self.interval = interval
        self.first_delay = first_delay
        self.last_error_day = ""

    def run_once(self) -> dict | None:
        if not self.enabled() or not self.backup.due() or not self.ready():
            return None
        try:
            result = self.backup.backup()
        except Exception as e:
            today = datetime.date.today().isoformat()
            if self.last_error_day != today:  # 失败一天只说一次,下个小时再试
                self.last_error_day = today
                self.report(f"自动备份到 Google Drive 没成功:{e}", True)
            return None
        self.report(f"已自动备份到 Google Drive(“{FOLDER_NAME}”文件夹,{len(result['files'])} 个文件)", False)
        return result

    def start(self) -> None:
        def run():
            time.sleep(self.first_delay)
            while True:
                try:
                    self.run_once()
                except Exception:
                    pass
                time.sleep(self.interval)

        threading.Thread(target=run, daemon=True, name="friday-backup").start()
