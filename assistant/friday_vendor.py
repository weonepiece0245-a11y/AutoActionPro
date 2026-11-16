"""3D 光球用的 three.js:第一次下载一份存在电脑上,以后都用本地的,不再每次从外面的网站拿。

这样即使那个网站以后被人动了手脚,星期五的窗口也不受影响;窗口的安全规则(CSP)也就可以只允许本机的程序。
还没下载好时(比如第一次打开时没网)照旧用网站上的,光球不受影响。
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import urllib.request
from pathlib import Path
from typing import Callable

THREE_VERSION = "0.160.0"
CDN = f"https://cdn.jsdelivr.net/npm/three@{THREE_VERSION}/"
ENTRY = [
    "build/three.module.js",
    "examples/jsm/postprocessing/EffectComposer.js",
    "examples/jsm/postprocessing/RenderPass.js",
    "examples/jsm/postprocessing/UnrealBloomPass.js",
    "examples/jsm/postprocessing/OutputPass.js",
]
MAX_FILE = 3 * 1024 * 1024
MAX_FILES = 40
IMPORT_RE = re.compile(r"""(?:import|export)\s[^'";]*?from\s*['"](\.{1,2}/[^'"]+)['"]|import\s*['"](\.{1,2}/[^'"]+)['"]""")
SAFE_PATH = re.compile(r"^(build|examples/jsm)/[A-Za-z0-9_/.-]+\.js$")
MARKER = "complete.json"

Fetch = Callable[[str], bytes]


def vendor_dir(web_dir: Path) -> Path:
    return Path(web_dir) / "vendor" / f"three-{THREE_VERSION}"


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "friday-assistant"})
    with urllib.request.urlopen(request, timeout=30) as response:
        data = response.read(MAX_FILE + 1)
    if len(data) > MAX_FILE:
        raise RuntimeError("文件太大")
    return data


def _relative_imports(path: str, code: str) -> list[str]:
    base = path.rsplit("/", 1)[0]
    found = []
    for match in IMPORT_RE.finditer(code):
        rel = match.group(1) or match.group(2)
        parts = base.split("/")
        for piece in rel.split("/"):
            if piece == "..":
                parts = parts[:-1]
            elif piece != ".":
                parts.append(piece)
        found.append("/".join(parts))
    return found


def is_complete(web_dir: Path) -> bool:
    return (vendor_dir(web_dir) / MARKER).exists()


def ensure(web_dir: Path, fetcher: Fetch = fetch) -> bool:
    """下载 three.js 和光球用到的附加程序(连同它们引用的文件)。已经有了就什么也不做。"""
    target = vendor_dir(web_dir)
    if (target / MARKER).exists():
        return True
    todo, done = list(ENTRY), {}
    while todo:
        path = todo.pop()
        if path in done:
            continue
        if not SAFE_PATH.match(path) or ".." in path or len(done) >= MAX_FILES:
            raise RuntimeError(f"不认识的文件:{path}")
        data = fetcher(CDN + path)
        code = data.decode("utf-8")
        if "export" not in code:
            raise RuntimeError(f"{path} 看起来不是 three.js 的文件")
        done[path] = data
        todo.extend(p for p in _relative_imports(path, code) if p not in done)
    for path, data in done.items():
        out = target / path
        out.parent.mkdir(parents=True, exist_ok=True)
        temp = out.with_name(out.name + ".tmp")
        temp.write_bytes(data)
        os.replace(temp, out)
    hashes = {p: hashlib.sha256(d).hexdigest() for p, d in done.items()}
    (target / MARKER).write_text(json.dumps({"version": THREE_VERSION, "files": hashes}, indent=1), encoding="utf-8")
    return True


def local_file(web_dir: Path, url_path: str) -> Path | None:
    """/vendor/three-0.160.0/build/three.module.js -> 本地文件(只认下载清单里的、而且内容没被改过)。"""
    prefix = f"/vendor/three-{THREE_VERSION}/"
    if not url_path.startswith(prefix):
        return None
    rel = url_path[len(prefix):]
    if not SAFE_PATH.match(rel) or ".." in rel:
        return None
    target = vendor_dir(web_dir)
    try:
        listed = json.loads((target / MARKER).read_text(encoding="utf-8"))["files"]
    except (OSError, ValueError, KeyError):
        return None
    if rel not in listed:
        return None
    path = target / rel
    try:
        if hashlib.sha256(path.read_bytes()).hexdigest() != listed[rel]:
            return None  # 被改过:不用
    except OSError:
        return None
    return path


# ======================== 窗口页面 + 安全规则 ========================

IMPORTMAP_RE = re.compile(r'<script type="importmap">(.*?)</script>', re.S)


def page(web_dir: Path) -> tuple[bytes, str]:
    """返回 (index.html 内容, Content-Security-Policy)。three.js 下载好了就改用本地的。"""
    html = (Path(web_dir) / "index.html").read_text(encoding="utf-8")
    local = is_complete(web_dir)
    if local:
        base = f"/vendor/three-{THREE_VERSION}/"
        html = html.replace(f"{CDN}build/three.module.js", f"{base}build/three.module.js")
        html = html.replace(f"{CDN}examples/jsm/", f"{base}examples/jsm/")
    match = IMPORTMAP_RE.search(html)
    hashes = ""
    if match:
        digest = base64.b64encode(hashlib.sha256(match.group(1).encode("utf-8")).digest()).decode()
        hashes = f" 'sha256-{digest}'"
    scripts = "'self'" + hashes + ("" if local else " https://cdn.jsdelivr.net")
    csp = ("default-src 'self'; "
           f"script-src {scripts}; "
           "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
           "font-src 'self' https://fonts.gstatic.com data:; "
           "img-src 'self' data: blob: https://i.scdn.co; media-src 'self' blob: data:; connect-src 'self'; "
           "worker-src 'self' blob:; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
    return html.encode("utf-8"), csp
