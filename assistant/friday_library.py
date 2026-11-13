"""我的资料库:你选几个文件夹(例如 文件、桌面),星期五把里面 PDF、Word、Excel、PPT、文字文件的内容整理成索引,
以后问“我的车险什么时候到期”“租约押金多少”,她先在你自己的文件里找,回答时告诉你是哪个文件第几页。

索引只存在你电脑上(friday_library.db),文件不上传;问的时候才把找到的那几小段给 Claude 看。
不收:钥匙、密码这类文件,星期五自己的文件,太大的文件(超过 40MB),扫描的图片 PDF(里面没有文字)。
每 6 小时自动更新一次(只看改过的文件);也可以说“更新资料库”。
"""

from __future__ import annotations

import datetime
import io
import os
import re
import sqlite3
import threading
import time
import zipfile
from html import unescape
from pathlib import Path
from typing import Callable

SUFFIXES = {".pdf", ".docx", ".xlsx", ".xlsm", ".pptx", ".txt", ".md", ".csv"}
MAX_FILE_MB = 40
MAX_FILES = 20000
MAX_PAGES = 400  # 一个 PDF 最多读几页
CHUNK = 900  # 一段大约几个字
SNIPPET = 700
REFRESH_HOURS = 6
SKIP_DIRS = {"appdata", "node_modules", ".git", "$recycle.bin", "system volume information", "windows", "program files",
             "program files (x86)", "programdata", "__pycache__", ".venv", "venv", "site-packages", "steamapps"}

# 名字像是放密码、备用码、助记词的文件:不收
PRIVATE_NAME = re.compile(r"密码|口令|password|passwd|passcode|credential|secret|备用码|恢复码|recovery.?code|backup.?code|"
                          r"助记词|seed.?phrase|mnemonic|2fa|otp|pin码|银行卡|信用卡号|身份证", re.I)
# 文件内容里像“密码:xxxx”的,给 Claude 看之前把后面的值遮掉
PRIVATE_LINE = re.compile(r"((?:密码|口令|password|passwd|passcode|pin|验证码|cvv|cvc|助记词|seed phrase|recovery code|api.?key|token)"
                          r"\s*[:：=]\s*)\S+", re.I)
UPDATE = re.compile(r"^(星期五)?[,，\s]*(更新|重新整理|整理|刷新|重建)(一下)?(我的)?资料库[。!！]?$")
STATUS = re.compile(r"^(星期五)?[,，\s]*(我的)?资料库(的)?(状态|怎么样了?|有多少|整理好了吗|好了没有?|里有什么)[?？]?$")


def default_folders() -> list[str]:
    home = Path.home()
    found = []
    for name in ("Documents", "Desktop", "OneDrive/Documents", "OneDrive/Desktop"):
        path = home / name
        if path.is_dir():
            found.append(str(path))
    return found


def folders_setting() -> list[str]:
    raw = os.environ.get("FRIDAY_LIBRARY_FOLDERS")
    if raw is None:
        return default_folders()
    return [f.strip() for f in raw.split("|") if f.strip()]


def library_enabled() -> bool:
    return os.environ.get("FRIDAY_LIBRARY", "on") != "off"


# ======================== 读文件里的字 ========================


def _xml_text(xml: str, para: str) -> str:
    xml = re.sub(para, "\n", xml)
    return unescape(re.sub(r"<[^>]+>", " ", xml))


def read_pages(path: Path) -> list[tuple[int, str]]:
    """[(第几页/第几段, 文字)]。PDF 按页;PPT 按张;Excel 按工作表;其他按 1。读不了抛异常。"""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        import pypdf

        reader = pypdf.PdfReader(str(path))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:
                raise ValueError("有密码") from None
        return [(i + 1, page.extract_text() or "") for i, page in enumerate(reader.pages[:MAX_PAGES])]
    if suffix in (".docx", ".pptx", ".xlsx", ".xlsm"):
        with zipfile.ZipFile(str(path)) as archive:
            names = archive.namelist()
            if suffix == ".docx":
                xml = archive.read("word/document.xml").decode("utf-8", errors="replace")
                return [(1, _xml_text(xml, r"</w:p>"))]
            if suffix == ".pptx":
                slides = sorted((n for n in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
                                key=lambda n: int(re.search(r"(\d+)", n.rsplit("/", 1)[1]).group(1)))
                return [(i + 1, _xml_text(archive.read(n).decode("utf-8", errors="replace"), r"</a:p>"))
                        for i, n in enumerate(slides)]
            shared = []
            if "xl/sharedStrings.xml" in names:
                xml = archive.read("xl/sharedStrings.xml").decode("utf-8", errors="replace")
                shared = [unescape(re.sub(r"<[^>]+>", "", s)) for s in re.findall(r"<si>(.*?)</si>", xml, re.S)]
            sheets = sorted((n for n in names if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n)),
                            key=lambda n: int(re.search(r"(\d+)", n.rsplit("/", 1)[1]).group(1)))
            pages = []
            for i, name in enumerate(sheets[:30]):
                xml = archive.read(name).decode("utf-8", errors="replace")
                rows = []
                for row in re.findall(r"<row[^>]*>(.*?)</row>", xml, re.S)[:3000]:
                    cells = []
                    for attrs, body in re.findall(r"<c([^>]*)>(.*?)</c>", row, re.S):
                        value = re.search(r"<v>(.*?)</v>", body, re.S)
                        inline = re.search(r"<t[^>]*>(.*?)</t>", body, re.S)
                        if 't="s"' in attrs and value:
                            try:
                                cells.append(shared[int(value.group(1))])
                            except (ValueError, IndexError):
                                pass
                        elif inline:
                            cells.append(unescape(inline.group(1)))
                        elif value:
                            cells.append(value.group(1))
                    if cells:
                        rows.append(" | ".join(cells))
                pages.append((i + 1, "\n".join(rows)))
            return pages
    data = path.read_bytes()[:5_000_000]
    for encoding in ("utf-8-sig", "gb18030", "big5"):
        try:
            return [(1, data.decode(encoding))]
        except UnicodeDecodeError:
            continue
    return [(1, data.decode("utf-8", errors="replace"))]


def clean_text(text: str) -> str:
    text = text.replace("\x00", " ")
    if re.search(r"[一-鿿]", text):  # 中文 PDF 每行都断开:接起来
        text = re.sub(r"(?<=[一-鿿,，])\n(?=[一-鿿])", "", text)
    return re.sub(r"[ \t　]+", " ", re.sub(r"\n\s*\n+", "\n", text)).strip()


def chunks(text: str, size: int = CHUNK) -> list[str]:
    """切成一段段(尽量在换行或句号断开)。"""
    text = clean_text(text)
    out = []
    while text:
        if len(text) <= size:
            out.append(text)
            break
        cut = max(text.rfind("\n", size // 2, size), text.rfind("。", size // 2, size), text.rfind(". ", size // 2, size))
        cut = cut + 1 if cut > 0 else size
        out.append(text[:cut].strip())
        text = text[cut:].strip()
    return [c for c in out if c]


# ======================== 资料库 ========================


class Library:
    def __init__(self, path: str | os.PathLike, folders: Callable[[], list[str]] = folders_setting,
                 skip: Callable[[Path], bool] = lambda p: False, reader: Callable[[Path], list] = read_pages,
                 clock: Callable[[], float] = time.time) -> None:
        self.path = Path(path)
        self.folders = folders
        self.skip = skip  # 钥匙、密码、星期五自己的文件
        self.reader = reader
        self.clock = clock
        self.lock = threading.Lock()
        self.busy = False
        self.progress = ""
        self.last_error = ""
        self.db = sqlite3.connect(str(self.path), check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS files (id INTEGER PRIMARY KEY, path TEXT UNIQUE, mtime REAL, size INTEGER,"
                        " pages INTEGER, status TEXT, indexed REAL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
        try:
            self.db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(text, file UNINDEXED, page UNINDEXED,"
                            " tokenize='trigram')")
            self.fts = True
        except sqlite3.OperationalError:  # 很旧的 SQLite 没有 trigram:用普通表,慢一点
            self.db.execute("CREATE TABLE IF NOT EXISTS chunks (text TEXT, file INTEGER, page INTEGER)")
            self.fts = False
        self.db.commit()

    # ---------- 整理(建索引) ----------
    def _walk(self) -> list[Path]:
        found = []
        for folder in self.folders():
            root = Path(folder)
            if not root.is_dir():
                continue
            for current, dirs, files in os.walk(root):
                dirs[:] = [d for d in dirs if not d.startswith((".", "$")) and d.lower() not in SKIP_DIRS]
                for name in files:
                    path = Path(current) / name
                    if (path.suffix.lower() in SUFFIXES and not name.startswith(("~$", ".")) and not self.skip(path)
                            and not PRIVATE_NAME.search(name)):
                        found.append(path)
                        if len(found) >= MAX_FILES:
                            return found
        return found

    def refresh(self, should_stop: Callable[[], bool] = lambda: False) -> dict:
        """只读新的和改过的文件;删掉已经不在的。返回 {added, updated, removed, failed}。"""
        with self.lock:
            if self.busy:
                return {"busy": True}
            self.busy = True
        stats = {"added": 0, "updated": 0, "removed": 0, "failed": 0}
        try:
            files = self._walk()
            with self.lock:
                known = {row[1]: row for row in self.db.execute("SELECT id, path, mtime, size FROM files")}
            seen = set()
            for n, path in enumerate(files, 1):
                if should_stop():
                    break
                key = str(path)
                seen.add(key)
                try:
                    stat = path.stat()
                except OSError:
                    continue
                old = known.get(key)
                if old and abs(old[2] - stat.st_mtime) < 1 and old[3] == stat.st_size:
                    continue
                self.progress = f"正在整理 {n}/{len(files)}:{path.name}"
                self._index(path, stat, old[0] if old else None, stats)
            for key, row in known.items():
                if key not in seen and not should_stop() and (not Path(key).exists() or not self._inside(Path(key))):
                    self._forget(row[0])
                    stats["removed"] += 1
            with self.lock:
                self.db.execute("INSERT OR REPLACE INTO meta VALUES ('refreshed', ?)", (str(self.clock()),))
                self.db.commit()
            self.last_error = ""
        except Exception as e:  # noqa: BLE001
            self.last_error = f"{type(e).__name__}: {e}"[:200]
        finally:
            self.progress = ""
            self.busy = False
        return stats

    def _inside(self, path: Path) -> bool:
        """还在选的文件夹里吗(文件夹从设定里拿掉了,里面的文件也从资料库拿掉)。"""
        for folder in self.folders():
            try:
                path.relative_to(folder)
                return not self.skip(path)
            except ValueError:
                continue
        return False

    def _forget(self, file_id: int) -> None:
        with self.lock:
            self.db.execute("DELETE FROM chunks WHERE file = ?", (file_id,))
            self.db.execute("DELETE FROM files WHERE id = ?", (file_id,))

    def _index(self, path: Path, stat, old_id: int | None, stats: dict) -> None:
        status, pages = "ok", []
        if stat.st_size > MAX_FILE_MB * 1024 * 1024:
            status = "太大"
        else:
            try:
                pages = [(page, chunks(text)) for page, text in self.reader(path)]
            except Exception as e:  # noqa: BLE001
                status = "读不了:" + (str(e)[:40] or type(e).__name__)
        if status == "ok" and not any(parts for _, parts in pages):
            status = "没有文字(可能是扫描的)"
        with self.lock:
            if old_id is not None:
                self.db.execute("DELETE FROM chunks WHERE file = ?", (old_id,))
                self.db.execute("UPDATE files SET mtime=?, size=?, pages=?, status=?, indexed=? WHERE id=?",
                                (stat.st_mtime, stat.st_size, len(pages), status, self.clock(), old_id))
                file_id = old_id
            else:
                cur = self.db.execute("INSERT INTO files (path, mtime, size, pages, status, indexed) VALUES (?,?,?,?,?,?)",
                                      (str(path), stat.st_mtime, stat.st_size, len(pages), status, self.clock()))
                file_id = cur.lastrowid
            for page, parts in pages:
                self.db.executemany("INSERT INTO chunks (text, file, page) VALUES (?,?,?)",
                                    [(part, file_id, page) for part in parts])
            self.db.commit()
        stats["updated" if old_id is not None else "added"] += 1
        if status != "ok":
            stats["failed"] += 1

    # ---------- 找 ----------
    def search(self, words: list[str], limit: int = 8) -> list[dict]:
        """关键词(同义词、英文写法都可以一起给)-> 最相关的几段:[{file, path, page, text, hits}]。"""
        words = [w.strip() for w in dict.fromkeys(str(w) for w in words) if 2 <= len(w.strip()) <= 40][:12]
        if not words:
            return []
        scores: dict[int, list] = {}
        with self.lock:
            for word in words:
                if self.fts and len(word) >= 3:
                    rows = self.db.execute("SELECT rowid, text FROM chunks WHERE chunks MATCH ? LIMIT 400",
                                           ('"' + word.replace('"', '""') + '"',)).fetchall()
                else:
                    pattern = "%" + word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
                    rows = self.db.execute("SELECT rowid, text FROM chunks WHERE text LIKE ? ESCAPE '\\' LIMIT 400",
                                           (pattern,)).fetchall()
                for rowid, text in rows:
                    entry = scores.setdefault(rowid, [0, 0, text])
                    entry[0] += 1
                    entry[1] += text.lower().count(word.lower())
            best = sorted(scores.items(), key=lambda kv: (-kv[1][0], -min(kv[1][1], 6)))[:limit]
            results = []
            for rowid, (hits, _, text) in best:
                row = self.db.execute("SELECT f.path, c.page FROM chunks c JOIN files f ON f.id = c.file WHERE c.rowid = ?",
                                      (rowid,)).fetchone()
                if row is None:
                    continue
                results.append({"file": Path(row[0]).name, "path": row[0], "page": int(row[1]), "hits": hits,
                                "text": snippet(text, words)})
        return results

    def read(self, name: str, page: int) -> str:
        """某个文件某一页的全部文字(给 Claude 看前后文)。"""
        with self.lock:
            rows = self.db.execute("SELECT f.path, c.text FROM chunks c JOIN files f ON f.id = c.file "
                                   "WHERE (f.path = ? OR f.path LIKE ?) AND c.page = ? ORDER BY c.rowid",
                                   (name, "%" + name, int(page))).fetchall()
        if not rows:
            raise ValueError(f"资料库里没有“{name}”第 {page} 页")
        text = hide_private("\n".join(r[1] for r in rows if r[0] == rows[0][0]))
        return text[:6000] + ("……(后面省略)" if len(text) > 6000 else "")

    def stats(self) -> dict:
        with self.lock:
            files = self.db.execute("SELECT COUNT(*), COALESCE(SUM(pages), 0) FROM files WHERE status = 'ok'").fetchone()
            failed = self.db.execute("SELECT COUNT(*) FROM files WHERE status != 'ok'").fetchone()[0]
            refreshed = self.db.execute("SELECT value FROM meta WHERE key = 'refreshed'").fetchone()
        when = ""
        if refreshed:
            when = datetime.datetime.fromtimestamp(float(refreshed[0])).strftime("%m-%d %H:%M")
        return {"files": files[0], "pages": files[1], "failed": failed, "refreshed": when, "busy": self.busy,
                "progress": self.progress, "error": self.last_error, "folders": self.folders()}

    def unreadable(self, limit: int = 20) -> list[tuple[str, str]]:
        with self.lock:
            return [(Path(p).name, s) for p, s in
                    self.db.execute("SELECT path, status FROM files WHERE status != 'ok' LIMIT ?", (limit,))]

    def describe(self) -> str:
        s = self.stats()
        if s["busy"]:
            return f"资料库正在整理:{s['progress'] or '刚开始'}。现在已经有 {s['files']} 个文件。"
        if not s["files"]:
            return "资料库还是空的。去 齿轮设定 → 生活 → 我的资料库 选文件夹,或者说“更新资料库”。"
        text = f"资料库有 {s['files']} 个文件(共 {s['pages']} 页/段),上次整理 {s['refreshed'] or '还没'}。"
        if s["failed"]:
            text += f"有 {s['failed']} 个读不了(扫描的、有密码的或太大的)。"
        return text

    def due(self) -> bool:
        with self.lock:
            row = self.db.execute("SELECT value FROM meta WHERE key = 'refreshed'").fetchone()
        return row is None or self.clock() - float(row[0]) >= REFRESH_HOURS * 3600

    def clear(self) -> None:
        with self.lock:
            self.db.execute("DELETE FROM chunks")
            self.db.execute("DELETE FROM files")
            self.db.execute("DELETE FROM meta")
            self.db.commit()


class Commands:
    """“更新资料库”“资料库状态”:直接回答,不问 Claude。"""

    def __init__(self, library: Library, refresh_soon: Callable[[], None]) -> None:
        self.library = library
        self.refresh_soon = refresh_soon

    def handle(self, text: str) -> str | None:
        text = str(text or "").strip()
        if UPDATE.match(text):
            if self.library.busy:
                return self.library.describe()
            self.refresh_soon()
            return "好,开始整理资料库(只看新的和改过的文件),整理好了你直接问就行。"
        if STATUS.match(text):
            return self.library.describe()
        return None


def hide_private(text: str) -> str:
    return PRIVATE_LINE.sub(lambda m: m.group(1) + "(已隐藏)", text)


def snippet(text: str, words: list[str], size: int = SNIPPET) -> str:
    """从这一段里截出关键词附近的文字(像密码的值先遮掉)。"""
    text = hide_private(text)
    if len(text) <= size:
        return text
    low = text.lower()
    hits = [low.find(w.lower()) for w in words if low.find(w.lower()) >= 0]
    start = max(0, min(hits) - size // 3) if hits else 0
    part = text[start:start + size]
    return ("……" if start else "") + part + ("……" if start + size < len(text) else "")


def describe_results(results: list[dict]) -> str:
    if not results:
        return "资料库里没找到。可以换几个关键词(同义词、英文或马来文写法)再找一次;或者这份文件不在资料库的文件夹里。"
    parts = []
    for i, r in enumerate(results, 1):
        where = f"第 {r['page']} 页" if r["path"].lower().endswith(".pdf") else (
            f"第 {r['page']} 张" if r["path"].lower().endswith(".pptx") else (
                f"第 {r['page']} 个工作表" if r["path"].lower().endswith((".xlsx", ".xlsm")) else ""))
        parts.append(f"[{i}] {r['file']}{(' ' + where) if where else ''}(对上 {r['hits']} 个关键词)\n{r['text']}")
    return "\n\n".join(parts)
