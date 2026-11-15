"""代码实验室的跑法(在另一个 Python 里跑,星期五自己不会被拖垮):

    python -I friday_lab_runner.py strict   <实验室文件夹> <脚本>
    python -I friday_lab_runner.py selftest <星期五副本文件夹>
    python -I friday_lab_runner.py verify   <星期五副本文件夹> <测试名>...   (经老板批准,不装安全网,只跑点名的测试)

先装一个“审计钩子”(Python 自带的 sys.addaudithook),跑的代码一碰到不该碰的就直接报错:
- 不能开别的程序(subprocess、os.system……)、不能用 ctypes / 注册表;
- 写文件、删文件、改名只能在实验室文件夹里(selftest 还可以写系统的临时文件夹);
- strict:不能上网;读文件只能读实验室文件夹和 Python 自己的库;
- selftest:只能连本机(127.0.0.1),跑星期五自己的测试用。测试里要开“实验室小程序”(和这个一模一样、
  也装着同样的安全网)时可以开,其他程序一样不行。
这是防“写错代码”的安全网,不是对付坏人的铁笼子;所以星期五也不会把你的钥匙、密码放进去。
"""

from __future__ import annotations

import hashlib
import os
import re
import sys
import tempfile


def _norm(path) -> str:
    try:
        return os.path.normcase(os.path.realpath(os.fsdecode(path)))
    except Exception:
        return ""


def _inside(path: str, roots: list[str]) -> bool:
    return any(path == root or path.startswith(root + os.sep) for root in roots)


BLOCKED: list[str] = []  # 这个测试里被安全网挡下的事(有的程序会把错误吞掉,报告里要看得出来是被挡住)


def _deny(message: str) -> None:
    BLOCKED.append(message)
    raise PermissionError(message)


def install(mode: str, workdir: str) -> None:
    work = _norm(workdir)
    temp = _norm(tempfile.gettempdir())
    write_roots = [work] + ([temp] if mode == "selftest" else [])
    import site

    libs = {sys.prefix, sys.base_prefix, sys.exec_prefix, sys.base_exec_prefix}
    try:
        libs.update(site.getsitepackages())
        libs.add(site.getusersitepackages())
    except Exception:
        pass
    # 只读得到实验室文件夹和 Python 自己的库(星期五自己的文件夹、.env、你的文件都读不到)
    read_roots = write_roots + [_norm(p) for p in libs if p]
    # 系统字体(画图要用中文字):只读
    windir = os.environ.get("WINDIR") or os.environ.get("SYSTEMROOT")
    fonts = [os.path.join(windir, "Fonts")] if windir else []
    if os.environ.get("LOCALAPPDATA"):
        fonts.append(os.path.join(os.environ["LOCALAPPDATA"], "Microsoft", "Windows", "Fonts"))
    fonts += ["/usr/share/fonts", "/usr/local/share/fonts", "/System/Library/Fonts", "/Library/Fonts"]
    read_roots += [_norm(p) for p in fonts]
    blocked_events = ("subprocess.Popen", "os.system", "os.exec", "os.spawn", "os.posix_spawn", "os.startfile", "os.fork",
                      "os.forkpty", "ctypes.", "_winapi.", "msvcrt.", "os.kill", "signal.pthread_kill",
                      # 注册表:读可以(Python 自己会读,例如文件类型),改不行
                      "winreg.CreateKey", "winreg.DeleteKey", "winreg.DeleteValue", "winreg.SetValue",
                      "winreg.ConnectRegistry", "winreg.LoadKey", "winreg.SaveKey", "winreg.DisableReflectionKey")
    # 自我测试里,测试要开的“实验室小程序”:只认副本里和这个一模一样的那份(它自己也会装安全网)
    allowed_runner = None
    if mode == "selftest":
        try:
            mine = hashlib.sha256(open(os.path.abspath(__file__), "rb").read()).hexdigest()
            copy = os.path.join(workdir, "friday_lab_runner.py")
            if hashlib.sha256(open(copy, "rb").read()).hexdigest() == mine:
                allowed_runner = _norm(copy)
        except OSError:
            pass
    import subprocess

    python = _norm(sys.executable)

    def approved(args) -> bool:
        """这个指令是不是“用同一个 Python 开实验室小程序”。"""
        if allowed_runner is None or not isinstance(args, (list, tuple)) or len(args) < 4:
            return False
        try:
            parts = [os.fspath(a) for a in args]
        except TypeError:
            return False
        if _norm(parts[0]) != python:
            return False
        return any(_norm(parts[i]) == allowed_runner and parts[i + 1] in ("strict", "selftest")
                   for i in range(1, len(parts) - 1))

    def approved_in_stack() -> bool:
        """现在是不是正在开 / 停一个“实验室小程序”(看是谁在叫)。"""
        if allowed_runner is None:
            return False
        frame = sys._getframe(2)
        while frame is not None:
            if frame.f_code.co_filename.endswith("subprocess.py"):
                obj = frame.f_locals.get("self")
                if isinstance(obj, subprocess.Popen):
                    return approved(getattr(obj, "args", None))
            frame = frame.f_back
        return False

    # 开实验室小程序时 Windows 会用到的(管道、开程序、超时停掉它)
    process_events = {"_winapi.CreateProcess", "_winapi.CreatePipe", "_winapi.TerminateProcess",
                      "msvcrt.open_osfhandle", "msvcrt.get_osfhandle", "os.kill", "os.posix_spawn"}
    harmless_events = {"ctypes.create_unicode_buffer", "ctypes.create_string_buffer",  # 只是一块记忆体
                       "msvcrt.locking"}  # 锁住自己开着的文件(“只开一个星期五”用的)
    harmless = {_norm(os.devnull)}  # “空设备”:读写都没东西
    local_hosts = {"127.0.0.1", "localhost", "::1", "0.0.0.0", ""}
    path_events = {"os.remove": 0, "os.rmdir": 0, "os.mkdir": 0, "os.chmod": 0, "os.chown": 0, "os.rename": 0,
                   "os.replace": 0, "os.link": 0, "os.symlink": 0, "os.truncate": 0, "shutil.rmtree": 0,
                   "shutil.copyfile": 1, "shutil.move": 1, "os.utime": 0}

    def hook(event: str, args: tuple) -> None:
        if event in harmless_events:
            return
        # Windows 的 Python 会先把指令拼成一串文字才检查:那就看是谁在开(Popen 自己记着原来的指令)
        if event == "subprocess.Popen" and len(args) > 1 and (approved(args[1]) or approved_in_stack()):
            return
        if event in process_events and approved_in_stack():
            return
        if event == "_winapi.CopyFile2" and len(args) >= 2:  # Windows 上复制文件(shutil.copy2):照读写的规矩看
            if _inside(_norm(args[0]), read_roots) and _inside(_norm(args[1]), write_roots):
                return
            _deny(f"代码实验室只能把看得到的文件复制到自己的文件夹:{args[1]}")
        if event.startswith(blocked_events):
            _deny(f"代码实验室不允许:{event}")
        if event == "open" and args:
            path = args[0]
            if isinstance(path, int):
                return
            mode_ = str(args[1] or "r") if len(args) > 1 else "r"
            flags = args[2] if len(args) > 2 and isinstance(args[2], int) else 0
            writing = any(c in mode_ for c in "wax+") or bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT))
            real = _norm(path)
            if real in harmless or (not writing and os.path.basename(real) == "mime.types"):
                return
            if writing and not _inside(real, write_roots):
                _deny(f"代码实验室只能在自己的文件夹里写文件:{path}")
            if not writing and not _inside(real, read_roots):
                _deny(f"代码实验室不能读这里的文件:{path}(要用的文件请让星期五先放进实验室)")
            return
        if event in path_events and args:
            for target in args[: 2 if event in ("os.rename", "os.replace", "os.link", "os.symlink", "shutil.move") else 1]:
                if isinstance(target, int):
                    continue
                if not _inside(_norm(target), write_roots):
                    _deny(f"代码实验室只能改自己文件夹里的东西:{target}")
            return
        if event in ("socket.connect", "socket.sendto", "socket.bind") and len(args) > 1:
            address = args[1]
            host = address[0] if isinstance(address, tuple) and address else str(address)
            if mode == "strict" or str(host) not in local_hosts:
                _deny("代码实验室不能上网" + ("" if mode == "strict" else "(只能连本机)"))
        if event == "socket.getaddrinfo" and args and mode == "strict":
            _deny("代码实验室不能上网")

    sys.addaudithook(hook)


def _utf8_output() -> None:
    """输出一律用 UTF-8、一行一行送出去。

    用 -E 开(不读环境变量,比较安全),PYTHONIOENCODING 也会被忽略:Windows 上默认不是 UTF-8,
    印到 ✅、❌ 这种字就当场出错;输出也会攒在一起最后才送,实验窗口就没办法边跑边看。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
        except (AttributeError, ValueError):
            pass


def main(argv: list[str]) -> int:
    _utf8_output()
    mode, workdir = argv[0], argv[1]
    os.chdir(workdir)
    here = _norm(os.path.dirname(os.path.abspath(__file__)))
    sys.path[:] = [p for p in sys.path if p and _norm(p) != here]  # 不能 import 星期五自己的程序
    if mode == "strict":
        script = argv[2]
        sys.path.insert(0, workdir)
        code = open(script, encoding="utf-8").read()
        install("strict", workdir)
        namespace = {"__name__": "__main__", "__file__": script}
        exec(compile(code, script, "exec"), namespace)  # noqa: S102
        return 0
    if mode in ("selftest", "verify"):
        sys.path.insert(0, workdir)
        # verify:实验室里跑不了的那几个测试,经老板批准后在实验室外面补跑(不装安全网,只跑点名的测试)
        if mode == "selftest":
            install("selftest", workdir)
        import traceback
        import unittest

        try:
            suite = (unittest.defaultTestLoader.loadTestsFromNames(argv[2:]) if mode == "verify"
                     else unittest.defaultTestLoader.loadTestsFromName("test_assistant"))
        except BaseException:  # noqa: BLE001  测试档案本身载入不了:把原因印出来
            traceback.print_exc(file=sys.stdout)
            print("\n@@LOADFAIL")
            return 1

        def flatten(item):
            if isinstance(item, unittest.TestSuite):
                for sub in item:
                    yield from flatten(sub)
            else:
                yield item

        broken = [t for t in flatten(suite) if type(t).__name__ == "_FailedTest"]
        if broken:  # unittest 把载入失败包成一个“失败的测试”:当成没跑起来
            for test in broken:
                print(getattr(test, "_exception", test))
            print("\n@@LOADFAIL")
            return 1
        blocked_in: dict[str, str] = {}

        class Result(unittest.TextTestResult):
            def startTest(self, test):
                BLOCKED.clear()
                super().startTest(test)

            def stopTest(self, test):
                if BLOCKED:
                    blocked_in[test.id()] = BLOCKED[0]
                super().stopTest(test)

        result = unittest.TextTestRunner(stream=sys.stdout, verbosity=1, resultclass=Result).run(suite)
        print(f"\n@@RESULT ran={result.testsRun} failures={len(result.failures)} errors={len(result.errors)}")
        for test, trace in result.failures + result.errors:
            print(f"@@FAILED {test.id()}")
            # 为什么过不了(给帮你升级的人看):安全网挡住的那一行优先,不然用错误的最后一行
            lines = [line.strip() for line in str(trace).splitlines() if line.strip()]
            blocked = [line for line in lines if "代码实验室" in line]
            starts = [i for i, line in enumerate(lines)
                      if re.match(r"^[\w.]+(Error|Exception|Exit|Interrupt|Warning)\b", line)]
            why = " ".join(lines[starts[-1]:]) if starts else (lines or ["?"])[-1]
            if blocked:
                why = blocked[-1]
            elif test.id() in blocked_in:  # 错误被程序自己吞掉了,但测试中途确实被挡过
                why = f"{blocked_in[test.id()]}(测试里被挡住了,所以:{why})"
            print(f"@@WHY {test.id()}\t{why[:240]}")
        return 0 if result.wasSuccessful() else 1
    print("不认识的模式", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
