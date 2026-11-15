"""代码实验室:星期五自己写代码、在“隔离的小房间”里跑一次,成功或失败都告诉你。

两种用法:
1. 跑代码(run):算东西、整理 Excel / CSV、画图、转换格式……代码放进 friday_lab\\runs\\编号 文件夹里跑,
   不能开别的程序、不能上网、只能读写那个文件夹(你要它处理的文件,星期五先复制进去)。最多跑 2 分钟。
2. 自我测试(self_test):星期五想改自己的程序时,先把自己的程序复制一份到 friday_lab\\selftest,
   在副本上改,跑全部测试,和没改的副本比一比:改了以后有没有多坏掉的、修好了哪些。你真正的星期五一个字都不会动。
   结果和改了什么存成报告(friday_lab\\reports),你可以把报告发给帮你升级的人。

聊天里会出现一个“实验窗口”:看得到她写的代码、边跑边显示输出,跑完标 ✅ 或 ❌;画的图直接显示,
做好的文件可以“打开”或“存到下载”。失败了她会看错误、改好再跑(同一件事最多 3 次);
缺 pandas、openpyxl、matplotlib 这类常用库时,先问你要不要装(只装固定名单里的)。

安全:跑的代码碰不到你的钥匙、密码(.env 不会复制进去,也读不到外面的文件);这是防写错的安全网,不是防坏人的铁笼子。
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable

RUN_TIMEOUT = 120
SELFTEST_TIMEOUT = 900
MAX_OUTPUT = 8000
KEEP_RUNS = 30
MAX_FILES = 5
MAX_FILE_MB = 20
RUNNER = Path(__file__).with_name("friday_lab_runner.py")
APP_DIR = Path(__file__).resolve().parent
COPY_SUFFIXES = {".py", ".md", ".txt", ".html", ".js", ".css", ".json"}
MAX_ATTEMPTS = 3  # 同一件事最多试几次(失败了改代码再跑)
ATTEMPT_WINDOW = 20 * 60  # 秒:这段时间内同一个标题算同一件事
RUN_ID = re.compile(r"^\d{8}-\d{6}(-\d+)?$")
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}
# 跑出来的文件:只有这些类型可以帮你打开 / 存到下载(不会打开 .py、.bat、.exe 这类会执行的)
OPENABLE = set(IMAGE_TYPES) | {".csv", ".xlsx", ".xls", ".pdf", ".txt", ".docx", ".pptx", ".json", ".md", ".svg", ".zip"}
# 可以帮你装的库:import 的名字 -> pip 的名字
INSTALLABLE = {
    "pandas": "pandas", "numpy": "numpy", "openpyxl": "openpyxl", "xlrd": "xlrd", "xlsxwriter": "XlsxWriter",
    "matplotlib": "matplotlib", "seaborn": "seaborn", "scipy": "scipy", "docx": "python-docx", "pptx": "python-pptx",
    "pypdf": "pypdf", "PIL": "pillow", "tabulate": "tabulate", "dateutil": "python-dateutil", "pytz": "pytz",
    "statsmodels": "statsmodels", "sympy": "sympy", "qrcode": "qrcode", "chardet": "chardet", "lxml": "lxml",
    "bs4": "beautifulsoup4", "yaml": "PyYAML", "reportlab": "reportlab", "fpdf": "fpdf2", "networkx": "networkx",
}
MISSING = re.compile(r"No module named '([A-Za-z0-9_]+)")
NEVER_COPY = re.compile(r"^(\.env.*|friday_[a-z_]*\.json|.*token.*|credentials.*|google_.*\.json|.*\.pem|.*\.key|friday\.lock|"
                        r"friday_library\.db.*|friday_archive\.jsonl|friday_memory\.json.*)$", re.I)

ASK = re.compile(r"^(星期五)?[,，\s]*(代码实验室|实验室)(的)?(结果|记录|状态|跑得怎么样了?)?[?？]?$")


def _decode(data: bytes) -> str:
    for encoding in ("utf-8", "gbk"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _clip(text: str, limit: int = MAX_OUTPUT) -> str:
    return text if len(text) <= limit else text[: limit // 2] + "\n……(中间省略)……\n" + text[-limit // 2:]


def safe_env() -> dict:
    """给实验室用的环境变量:钥匙、口令一个都不带。"""
    keep = ("SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "TEMP", "TMP", "PATH", "PATHEXT", "COMSPEC", "LANG", "HOME",
            "USERPROFILE", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE")
    env = {k: v for k, v in os.environ.items() if k.upper() in keep}
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONUNBUFFERED"] = "1"  # 边跑边看输出
    env["MPLBACKEND"] = "Agg"  # matplotlib 不开窗口,直接存图
    return env


def clean_error(error: str, work: Path) -> str:
    """错误信息里去掉实验室自己的那几行和长长的文件夹路径,只留跟代码有关的。"""
    lines = (error or "").splitlines()
    out, skip = [], False
    for line in lines:
        if skip:
            skip = False
            if line.startswith("    "):
                continue
        if "friday_lab_runner.py" in line:
            skip = True
            continue
        out.append(line)
    text = "\n".join(out)
    for prefix in (str(work) + os.sep, str(work) + "/", str(work)):  # 先拿掉带斜线的
        text = text.replace(prefix, "")
    return text


def missing_module(error: str) -> str | None:
    match = MISSING.search(error or "")
    return match.group(1) if match else None


class Lab:
    def __init__(self, folder: str | os.PathLike, python: str = sys.executable, app_dir: Path = APP_DIR,
                 is_secret: Callable[[Path], bool] = lambda p: False, run=None, popen=subprocess.Popen,
                 on_done: Callable[[dict], None] = lambda record: None,
                 clock: Callable[[], datetime.datetime] = datetime.datetime.now) -> None:
        self.folder = Path(folder)
        self.python = python
        self.app_dir = Path(app_dir)
        self.is_secret = is_secret
        self.run_process = run or subprocess.run
        self.custom_run = run is not None  # 测试用:一次跑完,不边跑边看
        self.popen = popen
        self.on_event: Callable[[dict], None] | None = None  # 实验窗口:开始 / 输出 / 结束
        self.attempts: dict[str, tuple[int, float, bool]] = {}  # 标题 -> (第几次, 时间, 成功了没)
        self.on_done = on_done
        self.clock = clock
        self.lock = threading.Lock()
        self.busy: str | None = None
        self.baseline: dict | None = None  # 没改过的副本的测试结果(同一版程序只跑一次)
        try:
            self.history: list[dict] = json.loads((self.folder / "history.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.history = []

    def _save(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / "history.json").write_text(json.dumps(self.history[-KEEP_RUNS:], ensure_ascii=False, indent=1),
                                                  encoding="utf-8")

    def _record(self, record: dict) -> dict:
        with self.lock:
            self.history = (self.history + [record])[-KEEP_RUNS:]
            self._save()
        return record

    # ---------- 1. 跑代码 ----------
    def run_code(self, code: str, title: str = "", files: list[str] | None = None, timeout: int = 60) -> dict:
        code = str(code or "")
        if not code.strip():
            raise ValueError("没有代码")
        if len(code) > 60000:
            raise ValueError("代码太长了")
        key = re.sub(r"\s+", "", str(title or "跑代码"))[:60].lower()
        count, last, last_ok = self.attempts.get(key, (0, 0.0, True))
        attempt = count + 1 if (not last_ok and time.monotonic() - last < ATTEMPT_WINDOW) else 1
        if attempt > MAX_ATTEMPTS:
            raise ValueError(f"「{title}」已经试了 {MAX_ATTEMPTS} 次都失败,先停下来告诉老板卡在哪里、要不要换个做法")
        stamp = self.clock().strftime("%Y%m%d-%H%M%S")
        work = self.folder / "runs" / stamp
        n = 1
        while work.exists():  # 同一秒跑两次:各用各的文件夹
            n += 1
            work = self.folder / "runs" / f"{stamp}-{n}"
        work.mkdir(parents=True)
        copied = []
        for raw in (files or [])[:MAX_FILES]:
            path = Path(str(raw)).expanduser()
            if not path.is_file():
                raise ValueError(f"找不到文件:{raw}")
            if self.is_secret(path) or NEVER_COPY.match(path.name):
                raise ValueError(f"{path.name} 像是钥匙或密码文件,不放进实验室")
            if path.stat().st_size > MAX_FILE_MB * 1024 * 1024:
                raise ValueError(f"{path.name} 超过 {MAX_FILE_MB}MB")
            shutil.copy2(path, work / path.name)
            copied.append(path.name)
        script = work / "main.py"
        script.write_text(code, encoding="utf-8")
        before = {p.name for p in work.iterdir()}
        started = time.monotonic()
        timeout = max(5, min(int(timeout or 60), RUN_TIMEOUT))
        title = str(title or "跑代码")[:60]
        self._emit({"phase": "start", "run": work.name, "title": title, "attempt": attempt, "max": MAX_ATTEMPTS,
                    "code": code[:20000], "files": copied})
        cmd = [self.python, "-E", "-B", str(RUNNER), "strict", str(work), str(script)]
        try:
            out, err, code_ = self._execute(cmd, work, timeout, work.name)
        except OSError as e:  # 开不了 Python:窗口里的实验卡片也要结束
            out, err, code_ = "", f"开不了 Python:{e}", -1
        produced = sorted(p.name for p in work.iterdir()
                          if p.name not in before and not p.name.startswith(".") and p.name != "__pycache__")
        err = clean_error(err, work)
        missing = missing_module(err) if code_ != 0 else None
        record = {"kind": "run", "time": self.clock().strftime("%Y-%m-%d %H:%M"), "title": title,
                  "ok": code_ == 0, "seconds": round(time.monotonic() - started, 1), "code": code[:4000],
                  "output": _clip(out), "error": _clip(err, 3000), "files": copied, "produced": produced,
                  "folder": str(work), "run": work.name, "attempt": attempt}
        if missing:
            record["missing"] = missing
            if missing in INSTALLABLE:
                record["installable"] = INSTALLABLE[missing]
        self.attempts[key] = (attempt, time.monotonic(), record["ok"])
        self._emit({"phase": "end", "run": work.name, "ok": record["ok"], "seconds": record["seconds"],
                    "error": record["error"][-3000:], "attempt": attempt, "max": MAX_ATTEMPTS,
                    "produced": [{"name": n, "image": Path(n).suffix.lower() in IMAGE_TYPES,
                                  "openable": Path(n).suffix.lower() in OPENABLE} for n in produced[:20]],
                    "missing": missing, "installable": record.get("installable")})
        self._prune()
        return self._record(record)

    def _emit(self, event: dict) -> None:
        if self.on_event is not None:
            try:
                self.on_event({"type": "lab", **event})
            except Exception:
                pass

    def _execute(self, cmd: list[str], work: Path, timeout: int, run_id: str) -> tuple[str, str, int]:
        """跑代码;边跑边把输出送到实验窗口(每 0.3 秒送一次)。返回 (输出, 错误, 结束码)。"""
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        env = safe_env()
        env["MPLCONFIGDIR"] = str(work / ".matplotlib")  # matplotlib 的缓存放在实验室里
        if self.custom_run:
            try:
                proc = self.run_process(cmd, cwd=str(work), capture_output=True, timeout=timeout, env=env,
                                        stdin=subprocess.DEVNULL, creationflags=flags)
                out, err = _decode(proc.stdout), _decode(proc.stderr)
                if out:
                    self._emit({"phase": "out", "run": run_id, "text": _clip(out)})
                return out, err, proc.returncode
            except subprocess.TimeoutExpired:
                return "", f"超过 {timeout} 秒还没跑完,停掉了。", -1
        proc = self.popen(cmd, cwd=str(work), stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
                          stdin=subprocess.DEVNULL, creationflags=flags)
        errors: list[bytes] = []
        reader = threading.Thread(target=lambda: errors.append(proc.stderr.read()), daemon=True)
        reader.start()
        killed = threading.Event()

        def kill() -> None:
            killed.set()
            try:
                proc.kill()
            except OSError:
                pass

        timer = threading.Timer(timeout, kill)
        timer.start()
        lines: list[str] = []
        pending: list[str] = []
        size, last = 0, 0.0  # 第一行马上送
        try:
            for raw in iter(proc.stdout.readline, b""):
                line = _decode(raw)
                if size < MAX_OUTPUT * 4:  # 太多就不再收(免得把记忆体塞满)
                    lines.append(line)
                    size += len(line)
                    if size <= MAX_OUTPUT:
                        pending.append(line)
                if pending and time.monotonic() - last > 0.3:
                    self._emit({"phase": "out", "run": run_id, "text": "".join(pending)})
                    pending, last = [], time.monotonic()
            proc.wait()
        finally:
            timer.cancel()
            reader.join(timeout=5)
            for stream in (proc.stdout, proc.stderr):
                try:
                    stream.close()
                except (OSError, AttributeError):
                    pass
        if pending:
            self._emit({"phase": "out", "run": run_id, "text": "".join(pending)})
        err = _decode(b"".join(errors))
        if killed.is_set():
            return "".join(lines), (err + "\n" if err else "") + f"超过 {timeout} 秒还没跑完,停掉了。", -1
        return "".join(lines), err, proc.returncode

    # ---------- 跑出来的文件 ----------
    def produced_file(self, run_id: str, name: str) -> Path | None:
        """实验室跑出来的文件(只认记录里有的,不收路径)。"""
        run_id, name = str(run_id or ""), str(name or "")
        if not name or "/" in name or "\\" in name or name.startswith("."):
            return None
        if run_id == "reports":  # 自我测试的报告
            path = self.folder / "reports" / name
            return path if name.endswith(("自我测试.txt", "每周检讨.md")) and path.is_file() else None
        if not RUN_ID.match(run_id):
            return None
        with self.lock:
            known = any(r.get("kind") == "run" and Path(r.get("folder", "")).name == run_id and name in r.get("produced", [])
                        for r in self.history)
        if not known:
            return None
        path = self.folder / "runs" / run_id / name
        try:
            if not path.resolve().is_relative_to((self.folder / "runs" / run_id).resolve()) or not path.is_file():
                return None
        except OSError:
            return None
        return path

    def export(self, run_id: str, name: str, action: str, downloads: Path | None = None, opener=None) -> str:
        """打开(open)或存到下载(save)跑出来的文件;返回给你看的话。"""
        path = self.produced_file(run_id, name)
        if path is None:
            raise ValueError("找不到这个文件(可能太旧被清掉了)")
        if path.suffix.lower() not in OPENABLE:
            raise ValueError(f"{path.suffix} 这种文件不帮你打开(可能会执行程序)")
        if action == "save":
            target_dir = Path(downloads or Path.home() / "Downloads")
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / path.name
            n = 1
            while target.exists():
                n += 1
                target = target_dir / f"{path.stem} ({n}){path.suffix}"
            shutil.copy2(path, target)
            return f"存好了:{target}"
        if action == "open":
            (opener or getattr(os, "startfile"))(str(path))
            return f"打开了 {path.name}"
        raise ValueError("不支持的动作")

    # ---------- 装库 ----------
    def install(self, packages: list[str], confirm: Callable[[str], bool]) -> str:
        wanted = []
        allowed = {v.lower(): v for v in INSTALLABLE.values()} | {k.lower(): v for k, v in INSTALLABLE.items()}
        for raw in packages or []:
            name = allowed.get(str(raw).strip().lower())
            if name is None:
                raise ValueError(f"{raw} 不在可以装的名单里(只装常用的数据、表格、画图库)")
            if name not in wanted:
                wanted.append(name)
        if not wanted:
            raise ValueError("要说装哪个库")
        if not confirm(f"给代码实验室安装 Python 库:{'、'.join(wanted)}(从 PyPI 下载,大约 1–3 分钟)"):
            return "老板没有允许安装。可以换个不用这个库的写法。"
        self._emit({"phase": "install", "packages": wanted})
        try:
            proc = self.run_process([self.python, "-m", "pip", "install", "-q", "--disable-pip-version-check", *wanted],
                                    capture_output=True, timeout=600, stdin=subprocess.DEVNULL,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired:
            return "装了 10 分钟还没装好,停掉了(可能网络太慢)。"
        if proc.returncode != 0:
            return "没装成功:" + _decode(proc.stderr or b"").strip()[-600:]
        for key in list(self.attempts):  # 装好了:重新算次数
            self.attempts[key] = (0, 0.0, True)
        return f"装好了:{'、'.join(wanted)}。现在可以再跑一次。"

    def _prune(self) -> None:
        runs = sorted((self.folder / "runs").glob("*"))
        for old in runs[:-KEEP_RUNS]:
            shutil.rmtree(old, ignore_errors=True)

    # ---------- 2. 自我测试 ----------
    def _copy_app(self, target: Path) -> None:
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        target.mkdir(parents=True)
        for path in self.app_dir.iterdir():
            if path.is_file() and path.suffix.lower() in COPY_SUFFIXES and not NEVER_COPY.match(path.name) \
                    and not self.is_secret(path):
                shutil.copy2(path, target / path.name)
        web = self.app_dir / "web"
        if web.is_dir():
            shutil.copytree(web, target / "web")

    def _version(self) -> str:
        digest = hashlib.sha256()
        for path in sorted(self.app_dir.glob("*.py")):
            digest.update(path.name.encode())
            digest.update(path.read_bytes())
        return digest.hexdigest()[:16]

    @staticmethod
    def apply_edits(root: Path, edits: list[dict]) -> list[str]:
        """[{file, old, new}]:old 要在文件里刚好出现一次;old 空的话是新建文件。返回改了哪些文件。"""
        changed = []
        for i, edit in enumerate(edits or [], 1):
            name = str(edit.get("file") or "").strip().replace("\\", "/")
            if not re.fullmatch(r"(web/)?[A-Za-z0-9_\-]+\.(py|js|css|html|md)", name):
                raise ValueError(f"第 {i} 个修改:文件名不对({name}),只能改星期五程序文件夹里的 .py / web 里的文件")
            path = root / name
            old, new = str(edit.get("old") or ""), str(edit.get("new") or "")
            if not old:
                if path.exists():
                    raise ValueError(f"第 {i} 个修改:{name} 已经存在,要改的话给 old")
                path.write_text(new, encoding="utf-8")
            else:
                if not path.is_file():
                    raise ValueError(f"第 {i} 个修改:没有 {name}")
                text = path.read_text(encoding="utf-8")
                count = text.count(old)
                if count != 1:
                    raise ValueError(f"第 {i} 个修改:{name} 里要改的那段出现了 {count} 次(要刚好 1 次)")
                path.write_text(text.replace(old, new, 1), encoding="utf-8")
            changed.append(name)
        return changed

    def _run_tests(self, root: Path) -> dict:
        started = time.monotonic()
        try:
            proc = self.run_process([self.python, "-E", "-B", str(RUNNER), "selftest", str(root)], cwd=str(root),
                                    capture_output=True, timeout=SELFTEST_TIMEOUT, env=safe_env(), stdin=subprocess.DEVNULL,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            out = _decode(proc.stdout) + _decode(proc.stderr)
        except subprocess.TimeoutExpired:
            return {"ran": 0, "failed": [], "error": f"测试超过 {SELFTEST_TIMEOUT // 60} 分钟没跑完", "output": ""}
        found = re.search(r"@@RESULT ran=(\d+) failures=(\d+) errors=(\d+)", out)
        failed = re.findall(r"@@FAILED (\S+)", out)
        result = {"ran": int(found.group(1)) if found else 0, "failed": failed, "seconds": round(time.monotonic() - started),
                  "output": _clip(out, 6000)}
        if not found:
            tail = "\n".join(line for line in out.strip().splitlines()[-15:] if "@@LOADFAIL" not in line)
            reason = "测试档案载入不了" if "@@LOADFAIL" in out else "测试没跑完就停了"
            result["error"] = f"测试没跑起来({reason})" + (f",最后的输出:\n{tail[-1500:]}" if tail else "")
        return result

    @staticmethod
    def edits_text(edits: list[dict]) -> str:
        """改了什么(给实验窗口看):每个文件 原来 -> 改成。"""
        parts = []
        for edit in edits or []:
            old, new = str(edit.get("old") or ""), str(edit.get("new") or "")
            lines = [f"### {edit.get('file')}" + ("(新文件)" if not old else "")]
            lines += ["- " + line for line in old.splitlines()] if old else []
            lines += ["+ " + line for line in new.splitlines()]
            parts.append("\n".join(lines))
        return "\n\n".join(parts)

    def self_test(self, edits: list[dict], title: str = "") -> dict:
        """改一份副本、跑全部测试,和没改的比。很慢(几分钟),在后台跑。"""
        title = str(title or "自我测试")[:60]
        run_id = "st-" + self.clock().strftime("%Y%m%d-%H%M%S")
        started = time.monotonic()
        self._emit({"phase": "start", "run": run_id, "kind": "self_test", "title": title, "attempt": 1,
                    "max": MAX_ATTEMPTS, "code": self.edits_text(edits)[:20000], "files": []})

        def step(text: str) -> None:
            self._emit({"phase": "out", "run": run_id, "text": f"[{round(time.monotonic() - started)} 秒] {text}\n"})

        version = self._version()
        if self.baseline is None or self.baseline.get("version") != version:
            step("① 复制一份没改过的星期五,跑全部测试当对照(第一次要几分钟)…")
            base_root = self.folder / "selftest" / "base"
            self._copy_app(base_root)
            base = self._run_tests(base_root)
            self.baseline = {**base, "version": version} if base.get("ran") else None  # 没跑起来的不留着用
            step(f"   对照组:跑了 {base.get('ran', 0)} 个测试,本来就过不了 {len(base.get('failed') or [])} 个(这台电脑的环境)")
            if not base.get("ran"):  # 连没改过的都跑不起来:不是改坏的,是测试环境的问题
                step("   对照组没跑起来,这次比不了")
                return self._finish_self_test(run_id, started, title, edits, [], {
                    "ran": 0, "failed": [], "output": base.get("output", ""),
                    "error": "没改过的副本也跑不起来,是测试环境的问题,不是这次的修改:" + base.get("error", "")}, set())
        else:
            step("① 对照组用上次的结果(程序没变过)")
        root = self.folder / "selftest" / "changed"
        self._copy_app(root)
        try:
            changed = self.apply_edits(root, edits)
            step(f"② 在副本上改了:{'、'.join(changed)};③ 跑全部测试…")
            after = self._run_tests(root)
        except ValueError as e:
            step(f"② 改不进去:{e}")
            changed, after = [], {"ran": 0, "failed": [], "error": str(e), "output": ""}
        return self._finish_self_test(run_id, started, title, edits, changed, after, set(self.baseline.get("failed") or []))

    @staticmethod
    def related_tests(root: Path, changed: list[str], failed: set) -> list[str]:
        """改到的文件,有哪些相关的测试在这台电脑本来就过不了(这部分测不到)。

        看测试档案里每一组测试(class)有没有用到改的那个模块(assistant.py 是 app.)。"""
        try:
            text = (root / "test_assistant.py").read_text(encoding="utf-8")
        except OSError:
            return sorted(failed)
        sections = {}
        for match in re.finditer(r"^class (\w+)\(.*?(?=^class \w+\(|\Z)", text, re.S | re.M):
            sections[match.group(1)] = match.group(0)
        # 测试档案里给模块取的别名:tools = sys.modules["friday_tools"]、import friday_x as y
        aliases: dict[str, set] = {}
        for alias, module in re.findall(r'^(\w+)\s*=\s*sys\.modules\[["\'](\w+)["\']\]', text, re.M):
            aliases.setdefault(module, set()).add(alias + ".")
        for module, alias in re.findall(r"^import (\w+) as (\w+)", text, re.M):
            aliases.setdefault(module, set()).add(alias + ".")
        keys = set()
        for name in changed:
            stem = Path(name).stem
            if name.startswith("web/"):
                keys.add("friday_web")
            elif stem == "assistant":
                keys.add("app.")
            elif stem != "test_assistant":
                keys.add(stem)
                keys |= aliases.get(stem, set())
        # 只看 class 里面(有缩排的那几行),名字要整个对上(friday_lab 不算 friday_label)
        patterns = [re.compile(r"^[ \t]+.*" + (re.escape(k) if k.endswith(".") else r"\b" + re.escape(k) + r"\b"), re.M)
                    for k in keys]
        related = {cls for cls, body in sections.items() if any(p.search(body) for p in patterns)}
        return sorted(t for t in failed if len(t.split(".")) > 2 and t.split(".")[-2] in related)

    def _finish_self_test(self, run_id: str, started: float, title: str, edits: list[dict], changed: list[str],
                          after: dict, before_failed: set) -> dict:
        def step(text: str) -> None:
            self._emit({"phase": "out", "run": run_id, "text": f"[{round(time.monotonic() - started)} 秒] {text}\n"})

        new_failures = [t for t in after.get("failed", []) if t not in before_failed]
        fixed = [t for t in before_failed if after.get("ran") and t not in after.get("failed", [])]
        ok = bool(after.get("ran")) and not after.get("error") and not new_failures
        # 测不到 = 相关的测试改之前、改之后都过不了(改之后通过了的就是修好了,不算)
        still_failing = before_failed & set(after.get("failed", []))
        blind = self.related_tests(self.folder / "selftest" / "changed", changed, still_failing) if ok else []
        record = {"kind": "self_test", "time": self.clock().strftime("%Y-%m-%d %H:%M"), "title": str(title or "自我测试")[:60],
                  "ok": ok, "ran": after.get("ran", 0), "baseline_failed": len(before_failed),
                  "new_failures": new_failures[:30], "fixed": fixed[:30], "changed": changed,
                  "error": after.get("error", ""), "output": after.get("output", ""), "blind": blind[:30],
                  "baseline_list": sorted(before_failed)[:80]}
        record["report"] = self._write_report(record, edits)
        if after.get("ran"):
            step(f"   改过的:跑了 {after['ran']} 个测试,用了 {after.get('seconds', 0)} 秒")
        if blind:
            step(f"   ⚠ 改到的地方有 {len(blind)} 个相关测试在这台电脑本来就过不了,这部分测不到")
        self._emit({"phase": "end", "run": run_id, "kind": "self_test", "ok": ok, "uncertain": bool(blind),
                    "seconds": round(time.monotonic() - started, 1), "error": "", "attempt": 1, "max": MAX_ATTEMPTS,
                    "summary": self.describe(record).split("。报告存在")[0][:2500] + "。真正的星期五没有被改。",
                    "failed": new_failures[:10] or blind[:10],
                    "produced": [{"name": Path(record["report"]).name, "image": False, "openable": True,
                                  "run": "reports"}]})
        return self._record(record)

    def _write_report(self, record: dict, edits: list[dict]) -> str:
        folder = self.folder / "reports"
        folder.mkdir(parents=True, exist_ok=True)
        stamp = f"{self.clock():%Y%m%d-%H%M%S}"
        path = folder / f"{stamp} 自我测试.txt"
        n = 1
        while path.exists():  # 同一秒两份:不要盖掉
            n += 1
            path = folder / f"{stamp}-{n} 自我测试.txt"
        lines = [f"星期五自我测试报告:{record['title']}", f"时间:{record['time']}", "",
                 f"结果:{'✅ 通过' if record['ok'] else '❌ 没通过'}",
                 f"跑了 {record['ran']} 个测试;没改之前本来就有 {record['baseline_failed']} 个过不了(这台电脑的环境)。"]
        if record["error"]:
            lines.append(f"问题:{record['error']}")
        if record["new_failures"]:
            lines += ["", "改了以后多坏掉的:"] + [f"- {t}" for t in record["new_failures"]]
        if record.get("blind"):
            lines[3] = "结果:⚠ 没法完全确认(没有多坏掉的,但相关测试本来就跑不了)"
            lines += ["", "改到的地方,这些相关测试在这台电脑本来就过不了(测不到):"] + [f"- {t}" for t in record["blind"]]
        if record.get("baseline_list"):  # 给帮你升级的人看:哪些测试在这台电脑跑不了
            lines += ["", f"这台电脑本来就过不了的测试({record['baseline_failed']} 个):"] + \
                [f"- {t}" for t in record["baseline_list"][:80]]
        if record["fixed"]:
            lines += ["", "改了以后修好的:"] + [f"- {t}" for t in record["fixed"]]
        lines += ["", "改了什么:"]
        for edit in edits or []:
            lines += [f"--- {edit.get('file')}", "【原来】", str(edit.get("old") or "(新文件)"), "【改成】", str(edit.get("new") or ""), ""]
        path.write_text("\n".join(lines), encoding="utf-8")
        return str(path)

    # ---------- 给你看 ----------
    @staticmethod
    def describe(record: dict) -> str:
        if record["kind"] == "run":
            head = f"{'✅' if record['ok'] else '❌'} 代码实验室「{record['title']}」{'跑成功了' if record['ok'] else '跑失败了'}({record['seconds']} 秒)"
            body = record["output"].strip()[-1500:] if record["ok"] else (record["error"].strip()[-1500:] or record["output"].strip()[-800:])
            extra = f"\n产生的文件:{'、'.join(record['produced'])}(在 {record['folder']})" if record.get("produced") else ""
            return f"{head}\n{body}{extra}" if body else head + extra
        head = f"{'✅' if record['ok'] else '❌'} 自我测试「{record['title']}」:"
        if record.get("error"):
            return head + f"没通过 —— {record['error']}。报告:{record['report']}"
        blind = record.get("blind") or []
        if record["ok"] and blind:
            head = f"⚠ 自我测试「{record['title']}」:"
            text = head + (f"没有多坏掉的测试,但没法完全确认 —— 改到的地方有 {len(blind)} 个相关测试在这台电脑本来就过不了"
                           f"(在实验室里跑不了),这部分改得对不对测不到")
        else:
            text = head + (f"通过了,{record['ran']} 个测试没有多坏掉的" if record["ok"] else f"有 {len(record['new_failures'])} 个测试被改坏了")
        if record["fixed"]:
            text += f";修好了 {len(record['fixed'])} 个"
        if record["new_failures"]:
            text += "(" + "、".join(t.rsplit(".", 1)[-1] for t in record["new_failures"][:5]) + ")"
        return text + f"。报告存在 {record['report']},可以把它发给帮你升级的人。"

    def recent(self, limit: int = 10) -> list[dict]:
        with self.lock:
            return list(reversed(self.history[-limit:]))

    def summary(self) -> str:
        items = self.recent(6)
        if not items:
            return "老板,实验室还空着呢,一次都没开过工~想算贷款、整理 Excel,直接跟我说“帮我算一下……”就好,我写好代码在这里跑给你看。"
        lines = [f"- {r['time']} {'✅' if r['ok'] else '❌'} {r['title']}" for r in items]
        return ("正在跑:" + self.busy + "\n" if self.busy else "") + "代码实验室最近:\n" + "\n".join(lines)

    # ---------- 在后台跑 ----------
    def start(self, label: str, job: Callable[[], dict]) -> str:
        with self.lock:
            if self.busy:
                return f"实验室正在跑「{self.busy}」,等它跑完再来。"
            self.busy = label

        def run() -> None:
            try:
                record = job()
            except Exception as e:  # noqa: BLE001
                record = self._record({"kind": "run", "time": self.clock().strftime("%Y-%m-%d %H:%M"), "title": label,
                                       "ok": False, "seconds": 0, "code": "", "output": "", "error": str(e)[:500],
                                       "files": [], "produced": [], "folder": ""})
            finally:
                self.busy = None
            try:
                self.on_done(record)
            except Exception:
                pass

        threading.Thread(target=run, daemon=True, name="friday-lab").start()
        return f"好,在代码实验室里跑「{label}」,跑完告诉你。"


class Commands:
    def __init__(self, lab: Lab) -> None:
        self.lab = lab

    def handle(self, text: str) -> str | None:
        if ASK.match(re.sub(r"\s+", "", str(text or ""))):
            return self.lab.summary()
        return None
