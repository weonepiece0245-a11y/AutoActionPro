"""代码实验室:星期五自己写代码、在“隔离的小房间”里跑一次,成功或失败都告诉你。

两种用法:
1. 跑代码(run):算东西、整理 Excel / CSV、画图、转换格式……代码放进 friday_lab\\runs\\编号 文件夹里跑,
   不能开别的程序、不能上网、只能读写那个文件夹(你要它处理的文件,星期五先复制进去)。最多跑 2 分钟。
2. 自我测试(self_test):星期五想改自己的程序时,先把自己的程序复制一份到 friday_lab\\selftest,
   在副本上改,跑全部测试,和没改的副本比一比:改了以后有没有多坏掉的、修好了哪些。你真正的星期五一个字都不会动。
   结果和改了什么存成报告(friday_lab\\reports),你可以把报告发给帮你升级的人。

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
    return env


class Lab:
    def __init__(self, folder: str | os.PathLike, python: str = sys.executable, app_dir: Path = APP_DIR,
                 is_secret: Callable[[Path], bool] = lambda p: False, run=subprocess.run,
                 on_done: Callable[[dict], None] = lambda record: None,
                 clock: Callable[[], datetime.datetime] = datetime.datetime.now) -> None:
        self.folder = Path(folder)
        self.python = python
        self.app_dir = Path(app_dir)
        self.is_secret = is_secret
        self.run_process = run
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
        try:
            proc = self.run_process([self.python, "-E", "-B", str(RUNNER), "strict", str(work), str(script)],
                                    cwd=str(work), capture_output=True, timeout=timeout, env=safe_env(),
                                    stdin=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            out, err, code_ = _decode(proc.stdout), _decode(proc.stderr), proc.returncode
        except subprocess.TimeoutExpired:
            out, err, code_ = "", f"超过 {timeout} 秒还没跑完,停掉了。", -1
        produced = sorted(p.name for p in work.iterdir() if p.name not in before)
        record = {"kind": "run", "time": self.clock().strftime("%Y-%m-%d %H:%M"), "title": str(title or "跑代码")[:60],
                  "ok": code_ == 0, "seconds": round(time.monotonic() - started, 1), "code": code[:4000],
                  "output": _clip(out), "error": _clip(err, 3000), "files": copied, "produced": produced,
                  "folder": str(work)}
        self._prune()
        return self._record(record)

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
            result["error"] = "测试没跑起来(程序可能有语法错误)"
        return result

    def self_test(self, edits: list[dict], title: str = "") -> dict:
        """改一份副本、跑全部测试,和没改的比。很慢(几分钟),在后台跑。"""
        version = self._version()
        if self.baseline is None or self.baseline.get("version") != version:
            base_root = self.folder / "selftest" / "base"
            self._copy_app(base_root)
            self.baseline = {**self._run_tests(base_root), "version": version}
        root = self.folder / "selftest" / "changed"
        self._copy_app(root)
        try:
            changed = self.apply_edits(root, edits)
            after = self._run_tests(root)
        except ValueError as e:
            changed, after = [], {"ran": 0, "failed": [], "error": str(e), "output": ""}
        before_failed = set(self.baseline.get("failed") or [])
        new_failures = [t for t in after.get("failed", []) if t not in before_failed]
        fixed = [t for t in before_failed if after.get("ran") and t not in after.get("failed", [])]
        ok = bool(after.get("ran")) and not after.get("error") and not new_failures
        record = {"kind": "self_test", "time": self.clock().strftime("%Y-%m-%d %H:%M"), "title": str(title or "自我测试")[:60],
                  "ok": ok, "ran": after.get("ran", 0), "baseline_failed": len(before_failed),
                  "new_failures": new_failures[:30], "fixed": fixed[:30], "changed": changed,
                  "error": after.get("error", ""), "output": after.get("output", "")}
        record["report"] = self._write_report(record, edits)
        return self._record(record)

    def _write_report(self, record: dict, edits: list[dict]) -> str:
        folder = self.folder / "reports"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{self.clock():%Y%m%d-%H%M%S} 自我测试.txt"
        lines = [f"星期五自我测试报告:{record['title']}", f"时间:{record['time']}", "",
                 f"结果:{'✅ 通过' if record['ok'] else '❌ 没通过'}",
                 f"跑了 {record['ran']} 个测试;没改之前本来就有 {record['baseline_failed']} 个过不了(这台电脑的环境)。"]
        if record["error"]:
            lines.append(f"问题:{record['error']}")
        if record["new_failures"]:
            lines += ["", "改了以后多坏掉的:"] + [f"- {t}" for t in record["new_failures"]]
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
            return "代码实验室还没跑过东西。叫我“算一下……”“把这个 Excel 整理成……”,我会写代码在实验室里跑。"
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
