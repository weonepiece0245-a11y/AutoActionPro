"""电脑关机也能用:云端的星期五当“后备”。

- 电脑是主人:电脑开着时,每分钟向云端报一次平安,并把记忆、提醒、待办、对话、Google 登录等同步过去。
- 云端只在超过 3 分钟没收到电脑的消息时(电脑关机、睡眠、断网)才接手:回答手机 Discord、按时提醒。
- 电脑回来后,先把云端这段时间的变化拿回来(新记住的事、提醒和待办、聊过的内容),再继续当主人。

两边用 HTTPS 连接:云端用自己生成的证书,电脑只认这张证书(配对码里有它的指纹),再加一串密码。
配对码在云端安装时显示,粘贴到电脑上星期五的齿轮设定里就连上了。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import http.client
import json
import os
import ssl
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

DEFAULT_PORT = 443
HEARTBEAT = 60  # 秒:电脑多久报一次平安
PC_STALE = 180  # 秒:这么久没消息,云端就接手
CODE_PREFIX = "FRIDAY-"
CERT_FILE, KEY_FILE = "cloud_cert.pem", "cloud_key.pem"
STATE_FILE = "friday_cloud_state.json"
# 电脑同步到云端的文件(云端只接受这些名字)
SYNC_FILES = [
    "friday_memory.json", "friday_reminders.json", "friday_todos.json", "friday_conversation.json",
    "friday_router.json", "friday_discord.json", "friday_archive.jsonl",
    "google_token.json", "google_login.json", "credentials.json",
]
MAX_SYNC_BYTES = 5 * 1024 * 1024  # 单个文件太大就不同步(聊天档案很久以后才会这么大)
# 云端接手期间可能改动、电脑回来时要拿回去的
TAKEOVER_FILES = ["friday_memory.json", "friday_reminders.json", "friday_todos.json", "friday_conversation.json"]


# ======================== 配对码 ========================


def make_code(host: str, port: int, secret: str, fingerprint: str) -> str:
    data = json.dumps({"h": host, "p": port, "s": secret, "f": fingerprint}, separators=(",", ":"))
    return CODE_PREFIX + base64.urlsafe_b64encode(data.encode()).decode().rstrip("=")


def parse_code(code: str) -> dict:
    code = "".join(code.split())
    if not code.startswith(CODE_PREFIX):
        raise ValueError("配对码要以 FRIDAY- 开头")
    raw = code[len(CODE_PREFIX):]
    try:
        data = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
        info = {"host": str(data["h"]), "port": int(data["p"]), "secret": str(data["s"]),
                "fingerprint": str(data["f"]).lower()}
    except (ValueError, KeyError, TypeError):
        raise ValueError("配对码不完整,请把整串复制过来") from None
    if not info["host"] or len(info["fingerprint"]) != 64 or len(info["secret"]) < 20:
        raise ValueError("配对码不完整,请把整串复制过来")
    return info


def cert_fingerprint(cert_file: str | Path) -> str:
    der = ssl.PEM_cert_to_DER_cert(Path(cert_file).read_text(encoding="ascii"))
    return hashlib.sha256(der).hexdigest()


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def read_files(folder: Path, names: list[str]) -> dict[str, str]:
    files = {}
    for name in names:
        path = folder / name
        if path.is_file() and path.stat().st_size <= MAX_SYNC_BYTES:
            files[name] = _b64(path.read_bytes())
    return files


def write_files(folder: Path, files: dict, allowed: list[str]) -> list[str]:
    written = []
    for name, content in files.items():
        if name not in allowed or not isinstance(content, str):
            continue
        target = folder / name
        temp = target.with_name(target.name + ".sync")
        temp.write_bytes(base64.b64decode(content))
        os.replace(temp, target)
        written.append(name)
    return written


# ======================== 云端 ========================


class CloudState:
    """云端记着:电脑最后一次报平安是什么时候、有没有接手过。"""

    def __init__(self, folder: Path, clock: Callable[[], float] = time.time) -> None:
        self.folder = Path(folder)
        self.clock = clock
        self.lock = threading.Lock()
        self.pc_last_seen = 0.0
        self.took_over_at: float | None = None
        try:
            data = json.loads((self.folder / STATE_FILE).read_text(encoding="utf-8"))
            self.pc_last_seen = float(data.get("pc_last_seen", 0))
            self.took_over_at = data.get("took_over_at")
        except (OSError, ValueError):
            pass

    def _save(self) -> None:
        try:
            (self.folder / STATE_FILE).write_text(json.dumps(
                {"pc_last_seen": self.pc_last_seen, "took_over_at": self.took_over_at}), encoding="utf-8")
        except OSError:
            pass

    def pc_online(self) -> bool:
        return self.clock() - self.pc_last_seen < PC_STALE

    def heartbeat(self, bye: bool = False) -> dict:
        """bye=True:电脑正常关掉星期五,云端马上接手,不用等 3 分钟。"""
        with self.lock:
            self.pc_last_seen = 0.0 if bye else self.clock()
            self._save()
            return {"took_over": self.took_over_at is not None}

    def take_over(self) -> bool:
        """云端要处理一件事了;第一次接手返回 True(要先换上电脑最后同步过来的资料)。"""
        with self.lock:
            if self.took_over_at is not None:
                return False
            self.took_over_at = self.clock()
            self._save()
            return True

    def changes(self) -> dict:
        """电脑回来了:把接手期间的变化交给它。"""
        with self.lock:
            since = self.took_over_at
        if since is None:
            return {"took_over": False}
        exchanges = []
        try:
            for line in (self.folder / "friday_archive.jsonl").read_text(encoding="utf-8").splitlines():
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if isinstance(entry, dict) and entry.get("t", 0) >= since:
                    exchanges.append(entry)
        except OSError:
            pass
        return {"took_over": True, "since": since, "files": read_files(self.folder, TAKEOVER_FILES),
                "exchanges": exchanges}

    def ack(self) -> None:
        with self.lock:
            self.took_over_at = None
            self._save()


def make_handler(state: CloudState, secret: str, on_sync: Callable[[list[str]], None],
                 on_update: Callable[[], dict] | None = None):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        timeout = 60
        server_version = "FridayCloud"

        def log_message(self, fmt, *args):  # 不刷屏
            pass

        def _json(self, status: int, data: dict) -> None:
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self) -> bool:
            given = self.headers.get("X-Friday-Secret", "")
            return hmac.compare_digest(given.encode(), secret.encode())

        def do_GET(self):
            if not self._authorized():
                return self._json(403, {"error": "forbidden"})
            if self.path == "/changes":
                return self._json(200, state.changes())
            if self.path == "/status":
                return self._json(200, {"pc_online": state.pc_online(), "took_over": state.took_over_at is not None})
            return self._json(404, {"error": "not found"})

        def do_POST(self):
            if not self._authorized():
                return self._json(403, {"error": "forbidden"})
            length = int(self.headers.get("Content-Length") or 0)
            if length > 64 * 1024 * 1024:
                return self._json(413, {"error": "too large"})
            try:
                data = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                return self._json(400, {"error": "bad json"})
            if self.path == "/heartbeat":
                return self._json(200, state.heartbeat(bool(data.get("bye"))))
            if self.path == "/sync":
                written = write_files(state.folder, data.get("files") or {}, SYNC_FILES)
                on_sync(written)
                return self._json(200, {"written": written})
            if self.path == "/ack":
                state.ack()
                return self._json(200, {"ok": True})
            if self.path == "/update" and on_update is not None:
                return self._json(200, on_update())
            return self._json(404, {"error": "not found"})

    return Handler


class TLSServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, handler, context: ssl.SSLContext) -> None:
        self.context = context
        super().__init__(address, handler)

    def get_request(self):
        sock, address = self.socket.accept()
        # 握手放到处理线程里做,慢的连接不会卡住别人
        return self.context.wrap_socket(sock, server_side=True, do_handshake_on_connect=False), address


def start_server(folder: Path, secret: str, state: CloudState, on_sync: Callable[[list[str]], None],
                 port: int = DEFAULT_PORT, host: str = "0.0.0.0",
                 on_update: Callable[[], dict] | None = None) -> TLSServer:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(Path(folder) / CERT_FILE), str(Path(folder) / KEY_FILE))
    server = TLSServer((host, port), make_handler(state, secret, on_sync, on_update), context)
    threading.Thread(target=server.serve_forever, daemon=True, name="friday-cloud").start()
    return server


# ======================== 电脑 ========================


class CloudClient:
    """电脑这边:报平安、同步资料、电脑回来时拿回云端的变化。"""

    def __init__(self, code: str, folder: Path, on_pull: Callable[[dict], None],
                 on_status: Callable[[str, bool], None] = lambda text, failed: None,
                 clock: Callable[[], float] = time.time) -> None:
        self.info = parse_code(code)
        self.folder = Path(folder)
        self.on_pull = on_pull
        self.on_status = on_status
        self.clock = clock
        self.connected = False
        self.error = ""
        self.last_ok: float | None = None
        self.pushed: dict[str, str] = {}  # 文件名 -> 上次同步时的内容摘要
        self.running = False
        self.wakeup = threading.Event()

    # ---------- 连接 ----------
    def request(self, method: str, path: str, body: dict | None = None, timeout: float = 30) -> dict:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE  # 用下面的指纹核对代替 CA 证书
        conn = http.client.HTTPSConnection(self.info["host"], self.info["port"], timeout=timeout, context=context)
        try:
            conn.connect()
            der = conn.sock.getpeercert(binary_form=True)
            if not der or hashlib.sha256(der).hexdigest() != self.info["fingerprint"]:
                raise ConnectionError("云端的证书对不上配对码(可能连错了服务器),为了安全没有发送任何资料")
            payload = json.dumps(body or {}).encode("utf-8") if method == "POST" else None
            conn.request(method, path, body=payload, headers={
                "X-Friday-Secret": self.info["secret"], "Content-Type": "application/json"})
            response = conn.getresponse()
            data = json.loads(response.read() or b"{}")
            if response.status == 403:
                raise ConnectionError("配对码的密码不对,请在云端重新拿一次配对码")
            if response.status != 200:
                raise ConnectionError(f"云端回应 {response.status}:{data.get('error', '')}")
            return data
        finally:
            conn.close()

    # ---------- 一轮 ----------
    def cycle(self) -> None:
        reply = self.request("POST", "/heartbeat", {"at": self.clock()})
        if reply.get("took_over"):
            changes = self.request("GET", "/changes")
            if changes.get("took_over"):
                self.on_pull(changes)
                self.pushed.clear()  # 合并后的资料要重新同步一遍
            self.request("POST", "/ack", {})
        self.push()

    def push(self) -> list[str]:
        files = read_files(self.folder, SYNC_FILES)
        digests = {name: hashlib.sha256(content.encode()).hexdigest() for name, content in files.items()}
        changed = {name: files[name] for name in files if self.pushed.get(name) != digests[name]}
        if changed:
            self.request("POST", "/sync", {"files": changed}, timeout=120)
            self.pushed.update({name: digests[name] for name in changed})
        return sorted(changed)

    def goodbye(self) -> None:
        """电脑这边的星期五要关了:告诉云端马上接手。"""
        try:
            self.request("POST", "/heartbeat", {"bye": True}, timeout=5)
        except Exception:
            pass

    def update_cloud(self) -> dict:
        return self.request("POST", "/update", {}, timeout=600)

    def run_once(self) -> bool:
        try:
            self.cycle()
        except Exception as e:
            was = self.connected
            self.connected, self.error = False, str(e) or type(e).__name__
            if was:
                self.on_status(f"和云端星期五断开了:{self.error}", True)
            return False
        if not self.connected:
            self.on_status("已连接云端星期五:电脑关机时由云端接手回答 Discord 和提醒", False)
        self.connected, self.error, self.last_ok = True, "", self.clock()
        return True

    def start(self) -> None:
        self.running = True

        def loop():
            while self.running:
                self.run_once()
                self.wakeup.wait(HEARTBEAT)
                self.wakeup.clear()

        threading.Thread(target=loop, daemon=True, name="friday-cloud-client").start()

    def stop(self) -> None:
        self.running = False
        self.wakeup.set()

    def state(self) -> dict:
        return {"connected": self.connected, "error": self.error, "host": self.info["host"], "last_ok": self.last_ok}


# ======================== 命令行:云端安装时打印配对码 ========================


def public_ip() -> str:
    import urllib.request

    for url in ("https://api.ipify.org", "https://ifconfig.me/ip", "https://icanhazip.com"):
        try:
            with urllib.request.urlopen(url, timeout=10) as response:
                ip = response.read().decode().strip()
                if ip:
                    return ip
        except Exception:
            continue
    return ""


def main(argv: list[str]) -> int:
    folder = Path(os.path.dirname(os.path.abspath(__file__)))
    if argv[:1] != ["code"]:
        print("用法:python friday_cloud.py code   (在云端服务器上显示配对码)")
        return 1
    secret = os.environ.get("FRIDAY_CLOUD_SECRET", "")
    if not secret and (folder / ".env").exists():
        for line in (folder / ".env").read_text(encoding="utf-8-sig").splitlines():
            if line.startswith("FRIDAY_CLOUD_SECRET="):
                secret = line.split("=", 1)[1].strip()
    host = argv[1] if len(argv) > 1 else public_ip()
    if not secret or not host or not (folder / CERT_FILE).exists():
        print("还没装好云端连接(缺密码、证书或者查不到公网 IP)。请重新运行 sudo bash server-install.sh")
        return 1
    port = int(os.environ.get("FRIDAY_CLOUD_PORT") or DEFAULT_PORT)
    print(make_code(host, port, secret, cert_fingerprint(folder / CERT_FILE)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
