"""“星期五”的图形界面:本机网页 + 应用模式窗口(不显示黑色命令行窗口)。

- 只监听 127.0.0.1,并且每次启动生成一个随机口令,网页的每个请求都要带上,
  这样别的网站没法借你的浏览器偷偷给“星期五”发指令。
- 浏览器通过 SSE(/events)实时收到回答、状态、确认请求等事件。
- 用 Edge 的应用模式打开一个独立窗口(没有地址栏);没有 Edge 就用默认浏览器。
"""

from __future__ import annotations

import collections
import json
import os
import queue
import re
import secrets
import shutil
import subprocess
import threading
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlparse

WEB_DIR = Path(os.path.dirname(os.path.abspath(__file__))) / "web"
DEFAULT_PORT = 47800
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/orb.js": ("orb.js", "text/javascript; charset=utf-8"),
}
APPROVAL_TIMEOUT = 300  # 秒:确认请求等这么久没人点,就当拒绝
MAX_UPLOAD = 25 * 1024 * 1024
HISTORY_TYPES = {"user", "done", "status", "error", "reminder", "cleared"}  # 重新打开窗口时补发的事件


class Hub:
    """把事件推给所有打开的窗口(SSE),并保留最近的对话,重新打开窗口时能看到。"""

    def __init__(self, history: int = 300) -> None:
        self.lock = threading.Lock()
        self.clients: set[queue.Queue] = set()
        self.history: collections.deque = collections.deque(maxlen=history)

    def broadcast(self, event: dict) -> None:
        event = {**event, "ts": time.time()}
        with self.lock:
            if event.get("type") in HISTORY_TYPES:
                if event["type"] == "cleared":
                    self.history.clear()
                self.history.append(event)
            clients = list(self.clients)
        for client in clients:
            client.put(event)

    def subscribe(self) -> tuple[queue.Queue, list[dict]]:
        client: queue.Queue = queue.Queue()
        with self.lock:
            self.clients.add(client)
            backlog = list(self.history)
        return client, backlog

    def unsubscribe(self, client: queue.Queue) -> None:
        with self.lock:
            self.clients.discard(client)

    @property
    def connected(self) -> int:
        with self.lock:
            return len(self.clients)


class AudioStore:
    """暂存要播放的语音(只保留最近几段)。"""

    def __init__(self, limit: int = 30) -> None:
        self.lock = threading.Lock()
        self.items: collections.OrderedDict[str, bytes] = collections.OrderedDict()
        self.limit = limit

    def put(self, data: bytes) -> str:
        key = uuid.uuid4().hex
        with self.lock:
            self.items[key] = data
            while len(self.items) > self.limit:
                self.items.popitem(last=False)
        return key

    def get(self, key: str) -> bytes | None:
        with self.lock:
            return self.items.get(key)


class SpeechStream:
    """边出字边朗读:回答一边生成,一边按句子切开交给后台线程合成语音,按顺序推给窗口。

    以前要等整段回答写完才开始合成,长回答要等十几秒甚至一分钟才出声。
    现在第一句写完就开始合成;后台线程忙的时候,后面写好的句子会攒成一段一起合成,
    这样请求次数少,而且窗口播放上一段时下一段已经在准备了。
    """

    SENTENCE_END = re.compile(r"[。！？!?；;…\n]|\.(?=\s)")
    MORE = "其余内容请看文字。"

    def __init__(self, synthesize: Callable[[str], bytes | None], publish: Callable[[bytes], None],
                 max_chars: int = 800, batch_chars: int = 200) -> None:
        self.synthesize = synthesize
        self.publish = publish
        self.max_chars = max_chars  # 一次回答最多读这么多字,太长的看文字
        self.batch_chars = batch_chars
        self.buffer = ""
        self.pending: collections.deque[str] = collections.deque()
        self.spoken = 0
        self.full = False
        self.finished = False
        self.cancelled = False
        self.wakeup = threading.Condition()
        self.thread = threading.Thread(target=self._run, daemon=True, name="friday-speech")
        self.thread.start()

    def feed(self, text: str) -> None:
        """收到回答的一小段文字。"""
        if self.full or self.cancelled or not text:
            return
        self.buffer += text
        while not self.full:
            end = self._sentence_end()
            if end is None:
                break
            sentence, self.buffer = self.buffer[:end], self.buffer[end:]
            self._queue(sentence)

    def _sentence_end(self) -> int | None:
        for match in self.SENTENCE_END.finditer(self.buffer):
            end = match.end()
            if self.buffer.count("```", 0, end) % 2 == 0:  # 代码块没结束前不切
                return end
        return None

    def _queue(self, sentence: str) -> None:
        if self.full or not sentence.strip():
            return
        with self.wakeup:
            if self.spoken + len(sentence) > self.max_chars:
                self.pending.append(self.MORE)
                self.full = True
            else:
                self.pending.append(sentence)
                self.spoken += len(sentence)
            self.wakeup.notify()

    def finish(self) -> None:
        """回答写完了:剩下没有标点结尾的部分也读出来。不等合成完就返回。"""
        if not self.full and not self.cancelled:
            rest, self.buffer = self.buffer, ""
            self._queue(rest)
        with self.wakeup:
            self.finished = True
            self.wakeup.notify()

    def cancel(self) -> None:
        """开始了新的问题:还没读的就不读了。"""
        with self.wakeup:
            self.cancelled = True
            self.pending.clear()
            self.wakeup.notify()

    def _next_batch(self) -> str | None:
        with self.wakeup:
            while not self.pending and not self.finished and not self.cancelled:
                self.wakeup.wait()
            if self.cancelled or not self.pending:
                return None
            batch = self.pending.popleft()
            while self.pending and len(batch) + len(self.pending[0]) <= self.batch_chars:
                batch += self.pending.popleft()
            return batch

    def _run(self) -> None:
        while True:
            batch = self._next_batch()
            if batch is None:
                return
            try:
                audio = self.synthesize(batch)
            except Exception:
                audio = None
            if audio and not self.cancelled:
                self.publish(audio)


class WebApprover:
    """在界面里弹出“授权请求”,等用户点允许或拒绝。

    remote 不为空时(正在回答手机 Discord 的消息),改为在 Discord 上问。
    """

    def __init__(self, hub: Hub, timeout: float = APPROVAL_TIMEOUT) -> None:
        self.hub = hub
        self.timeout = timeout
        self.allow_all = False
        self.remote: Callable[[str], bool] | None = None
        self.lock = threading.Lock()
        self.pending: dict[str, list] = {}

    def __call__(self, action: str) -> bool:
        if self.remote is not None:
            return self.remote(f"星期五请求{action}")
        if self.allow_all:
            return True
        return self._ask(action, always=False)

    def confirm(self, action: str) -> bool:
        if self.remote is not None:
            return self.remote(f"星期五请求{action}(这一步每次都要确认)")
        return self._ask(action, always=True)

    def _ask(self, action: str, always: bool) -> bool:
        if self.hub.connected == 0:
            return False  # 窗口没开,没人能确认
        request_id = uuid.uuid4().hex
        event = threading.Event()
        with self.lock:
            self.pending[request_id] = [event, None]
        self.hub.broadcast({"type": "approval", "id": request_id, "text": action, "always": always})
        event.wait(self.timeout)
        with self.lock:
            answer = self.pending.pop(request_id, [None, None])[1]
        self.hub.broadcast({"type": "approval_done", "id": request_id, "answer": answer or "timeout"})
        if answer == "all" and not always:
            self.allow_all = True
        return answer in ("yes", "all")

    def answer(self, request_id: str, value: str) -> bool:
        with self.lock:
            entry = self.pending.get(request_id)
            if entry is None:
                return False
            entry[1] = value
            entry[0].set()
        return True


class WebContext:
    """HTTP 处理器需要的东西。"""

    def __init__(self, hub: Hub, events: queue.Queue, approver: WebApprover, token: str, port: int,
                 state: Callable[[], dict], greeting: str = "",
                 transcribe: Callable[[bytes], str | None] | None = None,
                 synthesize: Callable[[str], bytes | None] | None = None) -> None:
        self.hub = hub
        self.events = events
        self.approver = approver
        self.token = token
        self.port = port
        self.state = state
        self.greeting = greeting
        self.transcribe = transcribe
        self.synthesize = synthesize
        self.audio = AudioStore()


def make_handler(ctx: WebContext):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "Friday"

        def log_message(self, fmt, *args):  # 不刷屏
            pass

        # ---- 工具方法 ----
        def _host_ok(self) -> bool:
            # 防 DNS 重绑定:只接受本机地址
            host = (self.headers.get("Host") or "").lower()
            return host in (f"127.0.0.1:{ctx.port}", f"localhost:{ctx.port}")

        def _token_ok(self, query: dict) -> bool:
            given = self.headers.get("X-Friday-Token") or (query.get("t") or [""])[0]
            return secrets.compare_digest(given, ctx.token)

        def _send(self, status: int, body: bytes, content_type: str, extra: dict | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, data) -> None:
            self._send(status, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def _body(self) -> bytes:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_UPLOAD:
                raise ValueError("上传的内容太大")
            return self.rfile.read(length) if length else b""

        # ---- GET ----
        def do_GET(self):  # noqa: N802
            if not self._host_ok():
                return self._send(403, b"forbidden", "text/plain")
            url = urlparse(self.path)
            query = parse_qs(url.query)
            if url.path in STATIC_FILES:
                name, content_type = STATIC_FILES[url.path]
                try:
                    body = (WEB_DIR / name).read_bytes()
                except OSError:
                    return self._send(404, b"not found", "text/plain")
                return self._send(200, body, content_type)
            if not self._token_ok(query):
                return self._send(403, b"forbidden", "text/plain")
            if url.path == "/events":
                return self._events()
            if url.path == "/api/state":
                return self._json(200, {**ctx.state(), "greeting": ctx.greeting})
            if url.path.startswith("/audio/"):
                data = ctx.audio.get(url.path[len("/audio/"):].split(".")[0])
                if data is None:
                    return self._send(404, b"not found", "text/plain")
                return self._send(200, data, "audio/mpeg")
            return self._send(404, b"not found", "text/plain")

        def _events(self) -> None:
            client, backlog = ctx.hub.subscribe()
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                for event in backlog:
                    self._sse(event)
                while True:
                    try:
                        event = client.get(timeout=15)
                    except queue.Empty:
                        self.wfile.write(b": ping\n\n")  # 心跳,检测窗口是否已关闭
                        self.wfile.flush()
                        continue
                    self._sse(event)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                pass
            finally:
                ctx.hub.unsubscribe(client)
                self.close_connection = True

        def _sse(self, event: dict) -> None:
            self.wfile.write(f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode("utf-8"))
            self.wfile.flush()

        # ---- POST ----
        def do_POST(self):  # noqa: N802
            if not self._host_ok():
                return self._send(403, b"forbidden", "text/plain")
            url = urlparse(self.path)
            if not self._token_ok(parse_qs(url.query)):
                return self._send(403, b"forbidden", "text/plain")
            try:
                body = self._body()
            except ValueError as e:
                return self._json(413, {"error": str(e)})
            try:
                if url.path == "/api/voice":
                    return self._voice(body)
                data = json.loads(body or b"{}")
            except (ValueError, UnicodeDecodeError):
                return self._json(400, {"error": "请求格式不对"})
            if url.path == "/api/ask":
                text = str(data.get("text") or "").strip()
                if not text:
                    return self._json(400, {"error": "内容是空的"})
                ctx.events.put(("web", {"text": text, "speak": bool(data.get("speak")), "source": "web"}))
                return self._json(202, {"ok": True})
            if url.path == "/api/approve":
                ok = ctx.approver.answer(str(data.get("id") or ""), str(data.get("answer") or "no"))
                return self._json(200 if ok else 404, {"ok": ok})
            if url.path == "/api/command":
                command = str(data.get("command") or "")
                if command not in ("clear",):
                    return self._json(400, {"error": "不支持的命令"})
                ctx.events.put(("command", command))
                return self._json(202, {"ok": True})
            if url.path == "/api/speak":
                text = str(data.get("text") or "").strip()
                audio = ctx.synthesize(text) if (ctx.synthesize and text) else None
                return self._json(200, {"url": f"/audio/{ctx.audio.put(audio)}.mp3" if audio else None})
            if url.path == "/api/shutdown":
                ctx.events.put(("eof", None))
                return self._json(200, {"ok": True})
            return self._json(404, {"error": "not found"})

        def _voice(self, audio: bytes) -> None:
            if not audio:
                return self._json(400, {"error": "没有收到声音"})
            if ctx.transcribe is None:
                return self._json(503, {"error": "语音识别不可用"})
            try:
                heard = ctx.transcribe(audio)
            except Exception as e:  # 识别失败把原因告诉界面
                return self._json(200, {"text": None, "error": str(e)})
            if heard:
                speak = self.headers.get("X-Speak") == "1"
                ctx.events.put(("web", {"text": heard, "speak": speak, "source": "voice"}))
            return self._json(200, {"text": heard})

    return Handler


def start_server(ctx: WebContext) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", ctx.port), make_handler(ctx))
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True, name="friday-web").start()
    return server


# ======================== 单实例和窗口 ========================


def new_token() -> str:
    return secrets.token_urlsafe(24)


def write_instance(path: Path, port: int, token: str) -> None:
    path.write_text(json.dumps({"port": port, "token": token, "pid": os.getpid()}), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def read_instance(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and "port" in data and "token" in data else None


def instance_alive(info: dict, timeout: float = 2.0) -> bool:
    """另一个星期五是否正在运行(用口令访问一下状态接口)。"""
    import urllib.request

    request = urllib.request.Request(
        f"http://127.0.0.1:{info['port']}/api/state", headers={"X-Friday-Token": info["token"]}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status == 200
    except Exception:
        return False


def app_url(port: int, token: str) -> str:
    return f"http://127.0.0.1:{port}/?t={token}"


def find_edge() -> str | None:
    if os.name != "nt":
        return None
    try:
        import winreg

        for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                key_path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe"
                with winreg.OpenKey(root, key_path) as key:
                    value, _ = winreg.QueryValueEx(key, "")
                if value and os.path.exists(value):
                    return value
            except OSError:
                continue
    except ImportError:
        pass
    for candidate in (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                      r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"):
        if os.path.exists(candidate):
            return candidate
    return shutil.which("msedge")


def open_window(url: str, profile_dir: Path, edge: str | None = None,
                popen: Callable = subprocess.Popen, browser_open: Callable = webbrowser.open) -> str:
    """用 Edge 应用模式打开独立窗口(单独的配置目录,不影响你平时的 Edge);没有 Edge 就用默认浏览器。"""
    edge = edge if edge is not None else find_edge()
    if edge:
        profile_dir.mkdir(parents=True, exist_ok=True)
        popen([edge, f"--app={url}", f"--user-data-dir={profile_dir}", "--window-size=1280,820",
               "--autoplay-policy=no-user-gesture-required", "--no-first-run", "--no-default-browser-check"])
        return "edge"
    browser_open(url)
    return "browser"
