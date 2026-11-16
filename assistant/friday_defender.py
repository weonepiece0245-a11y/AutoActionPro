"""病毒扫描:叫 Windows 自带的 Microsoft Defender 做快速扫描或全面扫描,扫完告诉你结果(不花钱)。

扫描在后台跑,不会卡住星期五;快速扫描一般几分钟,全面扫描要一两个小时甚至更久。
发现的威胁由 Defender 自己处理(隔离或删除),星期五只负责开始扫描和报告结果。
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from typing import Callable

KINDS = {"quick": ("QuickScan", "快速扫描"), "full": ("FullScan", "全面扫描")}
TIMEOUT = {"quick": 2 * 3600, "full": 24 * 3600}
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

PREFIX = "[Console]::OutputEncoding = [Text.Encoding]::UTF8; $ProgressPreference = 'SilentlyContinue'; "

STATUS_SCRIPT = PREFIX + r"""
$ErrorActionPreference = 'Stop'
$s = Get-MpComputerStatus
function T($d) { if ($d) { $d.ToString('yyyy-MM-dd HH:mm') } else { '' } }
[pscustomobject]@{
  enabled = [bool]$s.AntivirusEnabled
  realtime = [bool]$s.RealTimeProtectionEnabled
  signatures = (T $s.AntivirusSignatureLastUpdated)
  quick = (T $s.QuickScanEndTime)
  full = (T $s.FullScanEndTime)
} | ConvertTo-Json -Compress
"""

SCAN_SCRIPT = PREFIX + r"""
$ErrorActionPreference = 'Stop'
$start = (Get-Date).AddMinutes(-1)
Start-MpScan -ScanType __TYPE__
$found = @()
$details = $true
try {
  $hits = @(Get-MpThreatDetection | Where-Object { $_.InitialDetectionTime -ge $start })
  if ($hits.Count) {
    $names = @{}
    foreach ($t in @(Get-MpThreat)) { $names[[string]$t.ThreatID] = [string]$t.ThreatName }
    foreach ($d in $hits) {
      $found += [pscustomobject]@{
        name = [string]$names[[string]$d.ThreatID]
        files = @($d.Resources | ForEach-Object { [string]$_ })
        fixed = [bool]$d.ActionSuccess
      }
    }
  }
} catch { $details = $false }
[pscustomobject]@{ threats = @($found); details = $details } | ConvertTo-Json -Compress -Depth 4
"""

Runner = Callable[[str, float], "tuple[int, str, str]"]


def run_powershell(script: str, timeout: float) -> tuple[int, str, str]:
    proc = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                           "-Command", script], capture_output=True, timeout=timeout, creationflags=NO_WINDOW)
    return (proc.returncode, proc.stdout.decode("utf-8", errors="replace").strip(),
            proc.stderr.decode("utf-8", errors="replace").strip())


def available() -> bool:
    return os.name == "nt"


def _explain(error: str) -> str:
    low = error.lower()
    if "0x800106ba" in low or "not running" in low or "service" in low and "stop" in low:
        return "Defender 没在运行(可能装了别的杀毒软件,或者被关掉了)。打开“Windows 安全中心”看一下病毒和威胁防护。"
    if "is not recognized" in low or "commandnotfound" in low:
        return "这台电脑找不到 Defender 的指令,没办法扫描。"
    if "access" in low and "denied" in low or "0x80070005" in low:
        return "Windows 不让星期五开始扫描(权限不够)。可以在“Windows 安全中心 → 病毒和威胁防护”里手动扫描。"
    return "扫描没成功:" + (error.splitlines()[0][:150] if error else "不知道原因") + "。可以在“Windows 安全中心”里手动扫描。"


def _clean_file(path: str) -> str:
    for head in ("file:_", "containerfile:_", "process:_"):
        if path.startswith(head):
            return path[len(head):]
    return path


class Defender:
    def __init__(self, run: Runner | None = None, clock: Callable[[], float] = time.time) -> None:
        self.run = run or run_powershell
        self.clock = clock
        self._lock = threading.Lock()
        self.current: dict | None = None  # {"kind": "quick", "started": 时间}
        self.last: dict | None = None  # 上次扫描的结果 {"kind", "finished", "text", "threats"}

    # ---------- 状态 ----------
    def status(self) -> dict:
        code, out, err = self.run(STATUS_SCRIPT, 60)
        if code != 0 or not out:
            raise RuntimeError(_explain(err))
        return json.loads(out)

    def describe_status(self) -> str:
        parts = []
        if self.current:
            minutes = int((self.clock() - self.current["started"]) // 60)
            parts.append(f"正在做{KINDS[self.current['kind']][1]},已经 {minutes} 分钟了,扫完会告诉你。")
        elif self.last:
            parts.append("上次星期五帮你扫的结果:" + self.last["text"])
        try:
            s = self.status()
        except Exception as e:  # noqa: BLE001
            return "".join(parts) or str(e)
        if not s.get("enabled") or not s.get("realtime"):
            parts.append("⚠ Defender 的实时保护没开,打开“Windows 安全中心 → 病毒和威胁防护”把它打开。")
        else:
            parts.append("Defender 的实时保护开着。")
        parts.append(f"上次快速扫描:{s.get('quick') or '没有记录'};上次全面扫描:{s.get('full') or '没有记录'};"
                     f"病毒库更新于 {s.get('signatures') or '不知道'}。")
        return "".join(parts)

    # ---------- 扫描 ----------
    def scan(self, kind: str) -> dict:
        """在这里等到扫完(很久):-> {"ok", "threats": [{"name","files","fixed"}], "details", "error", "minutes"}"""
        started = self.clock()
        try:
            code, out, err = self.run(SCAN_SCRIPT.replace("__TYPE__", KINDS[kind][0]), TIMEOUT[kind])
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "扫描太久没扫完,星期五先不等了。可以在“Windows 安全中心”里看进度。",
                    "threats": [], "minutes": int((self.clock() - started) // 60)}
        minutes = int((self.clock() - started) // 60)
        if code != 0 or not out:
            return {"ok": False, "error": _explain(err), "threats": [], "minutes": minutes}
        try:
            data = json.loads(out)
        except ValueError:
            return {"ok": True, "threats": [], "details": False, "minutes": minutes}
        threats = data.get("threats") or []
        threats = [threats] if isinstance(threats, dict) else threats
        for t in threats:
            files = t.get("files") or []
            t["files"] = [_clean_file(f) for f in ([files] if isinstance(files, str) else files)]
        return {"ok": True, "threats": threats, "details": bool(data.get("details", True)), "minutes": minutes}

    @staticmethod
    def describe_result(kind: str, result: dict, name: str = "老板") -> str:
        label = KINDS[kind][1]
        if not result["ok"]:
            return f"{name},{label}没做成。{result['error']}"
        took = f"用了 {result['minutes']} 分钟" if result["minutes"] else "不到 1 分钟"
        threats = result["threats"]
        if not threats and not result.get("details", True):
            return (f"{name},{label}做完了({took})。星期五读不到这次找到的记录(Windows 不给看),"
                    "打开“Windows 安全中心 → 保护历史记录”看一下有没有发现东西;发现了的话 Windows 自己也会弹通知。")
        if not threats:
            return f"{name},{label}做完了({took}),没有发现病毒或威胁。"
        lines = []
        for t in threats[:5]:
            where = "、".join(t["files"][:2]) or "位置不明"
            lines.append(f"{t.get('name') or '未知威胁'}({where})" + (",已经处理掉了" if t.get("fixed") else ",还没处理"))
        unfixed = any(not t.get("fixed") for t in threats)
        tail = ("有的还没处理,马上打开“Windows 安全中心 → 保护历史记录”,按“删除”或“隔离”。" if unfixed
                else "Defender 已经自动处理了,想看详细可以打开“Windows 安全中心 → 保护历史记录”。")
        return f"⚠ {name},{label}做完了({took}),发现 {len(threats)} 个威胁:" + ";".join(lines) + "。" + tail

    def start(self, kind: str, on_done: Callable[[str, bool], None], name: str = "老板") -> str:
        """在后台开始扫描,马上返回;扫完调用 on_done(要说的话, 有没有发现威胁)。"""
        if kind not in KINDS:
            raise ValueError("只有快速扫描和全面扫描")
        if not available():
            return "病毒扫描只能在 Windows 电脑上做。"
        with self._lock:
            if self.current:
                minutes = int((self.clock() - self.current["started"]) // 60)
                return f"已经在做{KINDS[self.current['kind']][1]}了({minutes} 分钟),扫完会告诉你。"
            self.current = {"kind": kind, "started": self.clock()}

        def work() -> None:
            try:
                result = self.scan(kind)
            except Exception as e:  # noqa: BLE001
                result = {"ok": False, "error": _explain(str(e)), "threats": [], "minutes": 0}
            text = self.describe_result(kind, result, name)
            with self._lock:
                self.current = None
                self.last = {"kind": kind, "finished": self.clock(), "text": text, "threats": len(result["threats"])}
            on_done(text, bool(result["threats"]))

        threading.Thread(target=work, daemon=True, name="friday-virus-scan").start()
        if kind == "quick":
            return "开始快速扫描了,一般几分钟到十几分钟,扫完告诉你。"
        return "全面扫描开始了,要一两个小时甚至更久,这段时间电脑会慢一点。扫完会告诉你,也会推到手机。"

    def state(self) -> dict:
        running = None
        if self.current:
            running = {"kind": self.current["kind"], "minutes": int((self.clock() - self.current["started"]) // 60)}
        return {"running": running, "last": self.last["text"] if self.last else ""}
