"""星期五的守护程序:星期五万一崩溃或卡死,自动把它重新打开。

星期五启动时会顺便在后台开一个守护(没有窗口,只开一个)。星期五每 30 秒写一次“心跳”:
- 你点电源按钮正常关掉:心跳写着 quit,守护跟着结束;
- 检查更新后重启:心跳写着 restarting,守护等新版本起来;
- 进程不见了(崩溃),或者心跳 10 分钟没更新(卡死):守护把它重新打开(不弹窗口,像开机自动启动那样),
  下次启动时星期五会告诉你、也推到手机。15 分钟内最多重开 3 次,再不行就停下,免得一直开开关关。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

HEARTBEAT = "friday_heartbeat.json"
CRASH_NOTE = "friday_crash.json"
CHECK_EVERY = 30
HANG_SECONDS = 10 * 60
HANG_PINGS = 4  # 窗口的服务连续 4 次(大约 2 分钟)叫不动:当成卡住
RESTART_WAIT = 3 * 60  # 更新重启:最多等这么久新版本起来
MAX_RESTARTS = 3
RESTART_WINDOW = 15 * 60


def write_heartbeat(folder: Path, state: str = "running", argv: list[str] | None = None) -> None:
    data = {"time": time.time(), "pid": os.getpid(), "state": state,
            "exe": sys.executable, "argv": argv if argv is not None else sys.argv[1:],
            "script": os.path.join(os.path.dirname(os.path.abspath(__file__)), "assistant.py")}
    path = Path(folder) / HEARTBEAT
    temp = path.with_name(path.name + ".tmp")
    try:
        temp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(temp, path)
    except OSError:
        pass


def read_heartbeat(folder: Path) -> dict | None:
    try:
        data = json.loads((Path(folder) / HEARTBEAT).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def pid_alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


def kill(pid: int) -> None:
    if os.name == "nt":
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x0001, False, int(pid))  # PROCESS_TERMINATE
        if handle:
            ctypes.windll.kernel32.TerminateProcess(handle, 1)
            ctypes.windll.kernel32.CloseHandle(handle)
    else:
        try:
            os.kill(int(pid), 9)
        except OSError:
            pass


def relaunch(beat: dict) -> None:
    argv = [a for a in beat.get("argv") or [] if a not in ("--no-browser",)]
    for flag in ("--port",):  # 端口让它重新选
        if flag in argv:
            index = argv.index(flag)
            del argv[index:index + 2]
    command = [beat.get("exe") or sys.executable, beat.get("script"), *argv, "--no-browser"]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(command, cwd=os.path.dirname(beat.get("script") or "."), creationflags=flags)


def ping(folder: Path, beat: dict) -> bool | None:
    """敲一下星期五窗口的服务:有回应 True,叫不动 False,还不知道(刚开、还没写好)None。"""
    import urllib.request

    try:
        info = json.loads((Path(folder) / "friday_web.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if int(info.get("pid", 0)) != int(beat.get("pid", -1)):
        return None  # 不是现在这个星期五的(还在启动)
    request = urllib.request.Request(f"http://127.0.0.1:{int(info['port'])}/api/ping",
                                     headers={"X-Friday-Token": str(info.get("token", ""))})
    try:
        with urllib.request.urlopen(request, timeout=8) as response:  # noqa: S310
            return response.status == 200
    except Exception:
        return False


def rollback_update(beat: dict) -> bool:
    """刚更新完就出问题:退回上一版。"""
    script = beat.get("script")
    if not script:
        return False
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import friday_update

    return friday_update.rollback(Path(os.path.dirname(script)))


class Guard:
    """判断该不该重开(和真的进程、时间分开,方便测试)。"""

    def __init__(self, folder: Path, clock=time.time, alive=pid_alive, killer=kill, launcher=relaunch,
                 probe=ping, rollback=rollback_update) -> None:
        self.folder = Path(folder)
        self.clock = clock
        self.alive = alive
        self.killer = killer
        self.launcher = launcher
        self.probe = probe
        self.rollback = rollback
        self.restarts: list[float] = []
        self.misses = 0

    def note(self, reason: str) -> None:
        try:
            (self.folder / CRASH_NOTE).write_text(json.dumps({"time": self.clock(), "reason": reason},
                                                             ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass

    def step(self) -> str:
        """看一次:"ok" / "stop"(星期五正常关掉了,守护也结束)/ "restarted" / "gave_up"。"""
        beat = read_heartbeat(self.folder)
        if beat is None:
            return "stop"
        now = self.clock()
        age = now - float(beat.get("time", 0))
        state = beat.get("state")
        if state == "quit":
            return "stop"
        if state == "restarting":
            return "ok" if age < RESTART_WAIT else self._restart(beat, "检查更新后没能重新启动")
        if not self.alive(int(beat.get("pid", 0))):
            return self._restart(beat, "星期五意外关掉了")
        if age > HANG_SECONDS:
            self.killer(int(beat.get("pid", 0)))
            return self._restart(beat, f"星期五卡住超过 {HANG_SECONDS // 60} 分钟没反应")
        answered = self.probe(self.folder, beat) if state == "running" else None
        if answered is False:
            self.misses += 1
            if self.misses >= HANG_PINGS:
                self.misses = 0
                self.killer(int(beat.get("pid", 0)))
                return self._restart(beat, "星期五卡住了(窗口大约 2 分钟叫不动)")
        elif answered:
            self.misses = 0
        return "ok"

    def _restart(self, beat: dict, reason: str) -> str:
        now = self.clock()
        self.restarts = [t for t in self.restarts if now - t < RESTART_WINDOW]
        if len(self.restarts) >= MAX_RESTARTS:
            self.note(f"{reason},已经连续重开 {MAX_RESTARTS} 次还是不行,先停下来了")
            return "gave_up"
        self.restarts.append(now)
        try:
            if self.rollback(beat):  # 刚更新完就出问题:先退回上一版再开
                reason += ";因为刚更新完就出问题,已经自动退回上一版"
        except Exception:
            pass
        self.note(reason)
        # 新的那个起来之前标成“重启中”,免得下一次检查又重开一个
        data = {"time": now, "pid": 0, "state": "restarting", "exe": beat.get("exe"), "argv": beat.get("argv") or [],
                "script": beat.get("script")}
        try:
            (self.folder / HEARTBEAT).write_text(json.dumps(data), encoding="utf-8")
        except OSError:
            pass
        self.launcher(beat)
        return "restarted"


def start_guard(folder: Path, original_env: dict) -> None:
    """星期五启动时调用:后台开一个守护(已经有就不开)。"""
    command = [sys.executable, os.path.abspath(__file__), str(folder)]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
    try:
        subprocess.Popen(command, cwd=os.path.dirname(os.path.abspath(__file__)), env=original_env, creationflags=flags,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


def take_crash_note(folder: Path) -> str | None:
    """上次是不是守护重开的;是就返回原因(只返回一次)。"""
    path = Path(folder) / CRASH_NOTE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        path.unlink()
    except (OSError, ValueError):
        return None
    return str(data.get("reason") or "")


def main() -> None:
    folder = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import friday_web

    lock = friday_web.InstanceLock(folder / "friday_guard.lock")
    if not lock.acquire():
        return  # 已经有一个守护在看着
    guard = Guard(folder)
    own = Path(os.path.abspath(__file__))
    started = _mtime(own)
    time.sleep(CHECK_EVERY)
    while True:
        result = guard.step()
        if result in ("stop", "gave_up"):
            return
        if result == "ok" and _mtime(own) != started:  # 星期五更新过了:换新版的守护接着看
            lock.release()
            start_guard(folder, dict(os.environ))
            return
        time.sleep(CHECK_EVERY)


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


if __name__ == "__main__":
    main()
