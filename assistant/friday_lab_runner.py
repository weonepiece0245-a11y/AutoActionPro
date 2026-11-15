"""代码实验室的跑法(在另一个 Python 里跑,星期五自己不会被拖垮):

    python -I friday_lab_runner.py strict   <实验室文件夹> <脚本>
    python -I friday_lab_runner.py selftest <星期五副本文件夹>

先装一个“审计钩子”(Python 自带的 sys.addaudithook),跑的代码一碰到不该碰的就直接报错:
- 不能开别的程序(subprocess、os.system……)、不能用 ctypes / 注册表;
- 写文件、删文件、改名只能在实验室文件夹里(selftest 还可以写系统的临时文件夹);
- strict:不能上网;读文件只能读实验室文件夹和 Python 自己的库;
- selftest:只能连本机(127.0.0.1),跑星期五自己的测试用。
这是防“写错代码”的安全网,不是对付坏人的铁笼子;所以星期五也不会把你的钥匙、密码放进去。
"""

from __future__ import annotations

import os
import sys
import tempfile


def _norm(path) -> str:
    try:
        return os.path.normcase(os.path.realpath(os.fsdecode(path)))
    except Exception:
        return ""


def _inside(path: str, roots: list[str]) -> bool:
    return any(path == root or path.startswith(root + os.sep) for root in roots)


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
    blocked_events = ("subprocess.Popen", "os.system", "os.exec", "os.spawn", "os.posix_spawn", "os.startfile", "os.fork",
                      "os.forkpty", "ctypes.", "_winapi.", "msvcrt.", "os.kill", "signal.pthread_kill",
                      # 注册表:读可以(Python 自己会读,例如文件类型),改不行
                      "winreg.CreateKey", "winreg.DeleteKey", "winreg.DeleteValue", "winreg.SetValue",
                      "winreg.ConnectRegistry", "winreg.LoadKey", "winreg.SaveKey", "winreg.DisableReflectionKey")
    harmless = {_norm(os.devnull)}  # “空设备”:读写都没东西
    local_hosts = {"127.0.0.1", "localhost", "::1", "0.0.0.0", ""}
    path_events = {"os.remove": 0, "os.rmdir": 0, "os.mkdir": 0, "os.chmod": 0, "os.chown": 0, "os.rename": 0,
                   "os.replace": 0, "os.link": 0, "os.symlink": 0, "os.truncate": 0, "shutil.rmtree": 0,
                   "shutil.copyfile": 1, "shutil.move": 1, "os.utime": 0}

    def hook(event: str, args: tuple) -> None:
        if event.startswith(blocked_events):
            raise PermissionError(f"代码实验室不允许:{event}")
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
                raise PermissionError(f"代码实验室只能在自己的文件夹里写文件:{path}")
            if not writing and not _inside(real, read_roots):
                raise PermissionError(f"代码实验室不能读这里的文件:{path}(要用的文件请让星期五先放进实验室)")
            return
        if event in path_events and args:
            for target in args[: 2 if event in ("os.rename", "os.replace", "os.link", "os.symlink", "shutil.move") else 1]:
                if isinstance(target, int):
                    continue
                if not _inside(_norm(target), write_roots):
                    raise PermissionError(f"代码实验室只能改自己文件夹里的东西:{target}")
            return
        if event in ("socket.connect", "socket.sendto", "socket.bind") and len(args) > 1:
            address = args[1]
            host = address[0] if isinstance(address, tuple) and address else str(address)
            if mode == "strict" or str(host) not in local_hosts:
                raise PermissionError("代码实验室不能上网" + ("" if mode == "strict" else "(只能连本机)"))
        if event == "socket.getaddrinfo" and args and mode == "strict":
            raise PermissionError("代码实验室不能上网")

    sys.addaudithook(hook)


def main(argv: list[str]) -> int:
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
    if mode == "selftest":
        sys.path.insert(0, workdir)
        install("selftest", workdir)
        import unittest

        suite = unittest.defaultTestLoader.loadTestsFromName("test_assistant")
        result = unittest.TextTestRunner(stream=sys.stdout, verbosity=1).run(suite)
        print(f"\n@@RESULT ran={result.testsRun} failures={len(result.failures)} errors={len(result.errors)}")
        for test, _ in result.failures + result.errors:
            print(f"@@FAILED {test.id()}")
        return 0 if result.wasSuccessful() else 1
    print("不认识的模式", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
