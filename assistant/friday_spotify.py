"""Spotify 点歌:“放周杰伦的晴天”“放我的歌单 开车”“放我喜欢的歌”“现在放的是什么歌”(不花钱)。

要在 Spotify 开发者网站建一个免费的应用,把它的 Client ID 填进齿轮设定,再点“登录 Spotify”一次。
登录资料存在 friday_spotify.json(只在这台电脑,不备份、不读给 Claude)。
Spotify 只让 Premium 账号用程序直接播放;免费账号会把歌在 Spotify 里打开,你点一下播放。
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API = "https://api.spotify.com/v1"
SCOPES = "user-read-playback-state user-modify-playback-state user-read-currently-playing playlist-read-private user-library-read"
TOKEN_FILE = "friday_spotify.json"


class SpotifyError(Exception):
    pass


def client_id() -> str:
    return os.environ.get("FRIDAY_SPOTIFY_CLIENT_ID", "").strip()


def _request(method: str, url: str, headers: dict, body: bytes | None = None) -> tuple[int, dict]:
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310
            raw = response.read()
            return response.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        try:
            data = json.loads(e.read() or b"{}")
        except ValueError:
            data = {}
        return e.code, data
    except (urllib.error.URLError, OSError) as e:
        raise SpotifyError("连不上 Spotify,看看网络") from e


class Spotify:
    def __init__(self, folder: str | os.PathLike, port: int, request: Callable = _request,
                 open_uri: Callable[[str], None] | None = None, clock: Callable[[], float] = time.time) -> None:
        self.path = Path(folder) / TOKEN_FILE
        self.redirect = f"http://127.0.0.1:{port}/spotify/callback"
        self.request = request
        self.open_uri = open_uri or (lambda uri: os.startfile(uri))  # noqa: S606
        self.clock = clock
        self.pending: dict[str, str] = {}  # state -> code_verifier(登录进行中)
        self.lock = threading.Lock()
        try:
            self.tokens: dict = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.tokens = {}

    # ---------- 登录 ----------
    @property
    def connected(self) -> bool:
        return bool(self.tokens.get("refresh_token")) and bool(client_id())

    def login_url(self) -> str:
        if not client_id():
            raise SpotifyError("先在齿轮设定里填 Spotify 应用的 Client ID")
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        state = secrets.token_urlsafe(16)
        self.pending = {state: verifier}  # 只认最新的这一次
        query = urllib.parse.urlencode({"client_id": client_id(), "response_type": "code", "redirect_uri": self.redirect,
                                        "code_challenge_method": "S256", "code_challenge": challenge,
                                        "state": state, "scope": SCOPES})
        return f"{AUTH_URL}?{query}"

    def callback(self, query: dict) -> str:
        """Spotify 登录完跳回来:换成登录资料。返回给浏览器看的话。"""
        state = (query.get("state") or [""])[0]
        verifier = self.pending.pop(state, None)
        if verifier is None:
            return "这个登录链接已经失效了,请回到星期五的齿轮设定重新点“登录 Spotify”。"
        if query.get("error"):
            return "没有登录(你按了取消,或者 Spotify 拒绝了)。"
        code = (query.get("code") or [""])[0]
        body = urllib.parse.urlencode({"grant_type": "authorization_code", "code": code, "redirect_uri": self.redirect,
                                       "client_id": client_id(), "code_verifier": verifier}).encode()
        status, data = self.request("POST", TOKEN_URL, {"Content-Type": "application/x-www-form-urlencoded"}, body)
        if status != 200 or "access_token" not in data:
            return "登录没成功:Spotify 没给登录资料。检查 Client ID 和 Redirect URI 有没有填对。"
        self._store(data)
        return "Spotify 连好了,可以关掉这个网页,回去跟星期五说“放一首歌”。"

    def logout(self) -> str:
        self.tokens = {}
        try:
            self.path.unlink()
        except OSError:
            pass
        return "已经断开 Spotify。"

    def _store(self, data: dict) -> None:
        tokens = {"access_token": data["access_token"], "expires_at": self.clock() + int(data.get("expires_in", 3600)) - 60,
                  "refresh_token": data.get("refresh_token") or self.tokens.get("refresh_token", "")}
        self.tokens = tokens
        temp = self.path.with_name(f"{self.path.name}.{threading.get_ident()}.tmp")
        temp.write_text(json.dumps(tokens), encoding="utf-8")
        os.replace(temp, self.path)

    def _token(self) -> str:
        if not self.connected:
            raise SpotifyError("还没连 Spotify:齿轮设定 → 连接的应用 → 登录 Spotify")
        with self.lock:
            if self.clock() < self.tokens.get("expires_at", 0):
                return self.tokens["access_token"]
            body = urllib.parse.urlencode({"grant_type": "refresh_token", "refresh_token": self.tokens["refresh_token"],
                                           "client_id": client_id()}).encode()
            status, data = self.request("POST", TOKEN_URL, {"Content-Type": "application/x-www-form-urlencoded"}, body)
            if status != 200 or "access_token" not in data:
                raise SpotifyError("Spotify 登录过期了,到齿轮设定重新点“登录 Spotify”")
            self._store(data)
            return self.tokens["access_token"]

    def api(self, method: str, path: str, params: dict | None = None, body: dict | None = None) -> tuple[int, dict]:
        url = f"{API}{path}" + (f"?{urllib.parse.urlencode(params)}" if params else "")
        headers = {"Authorization": f"Bearer {self._token()}"}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode()
        elif method in ("PUT", "POST"):
            data = b""
        return self.request(method, url, headers, data)

    # ---------- 播放 ----------
    def _find(self, query: str, kind: str) -> dict | None:
        status, data = self.api("GET", "/search", {"q": query, "type": kind, "limit": 5})
        if status != 200:
            raise SpotifyError("Spotify 搜索失败,等一下再试")
        items = [i for i in (data.get(f"{kind}s") or {}).get("items") or [] if i]
        return items[0] if items else None

    def _my_playlist(self, name: str) -> dict | None:
        status, data = self.api("GET", "/me/playlists", {"limit": 50})
        if status != 200:
            return None
        want = name.lower().replace(" ", "")
        items = [p for p in data.get("items") or [] if p]
        exact = [p for p in items if p.get("name", "").lower().replace(" ", "") == want]
        part = [p for p in items if want in p.get("name", "").lower().replace(" ", "")]
        return (exact or part or [None])[0]

    def _start(self, body: dict, label: str, fallback_uri: str | None) -> str:
        status, data = self.api("PUT", "/me/player/play", body=body)
        if status in (200, 202, 204):
            return f"好,放{label}。"
        reason = (data.get("error") or {}).get("reason", "") if isinstance(data.get("error"), dict) else ""
        if status == 404 or reason == "NO_ACTIVE_DEVICE":
            device = self._device()
            if device:
                status, data = self.api("PUT", "/me/player/play", {"device_id": device}, body=body)
                if status in (200, 202, 204):
                    return f"好,放{label}。"
        if status == 403 or reason == "PREMIUM_REQUIRED":
            if fallback_uri:
                self.open_uri(fallback_uri)
                return f"已经在 Spotify 里打开{label}。免费账号 Spotify 不让程序直接播放,点一下播放就好。"
            return "Spotify 只让 Premium 账号用程序直接播放。"
        if fallback_uri:
            self.open_uri(fallback_uri)
            return f"Spotify 没在播放的设备,我把{label}在 Spotify 里打开了,点一下播放。"
        return "Spotify 没有在用的播放设备,先打开 Spotify 再说一次。"

    def _device(self) -> str | None:
        for attempt in range(2):
            status, data = self.api("GET", "/me/player/devices")
            devices = data.get("devices") or [] if status == 200 else []
            if devices:
                active = [d for d in devices if d.get("is_active")] or [d for d in devices if d.get("type") == "Computer"] or devices
                return active[0].get("id")
            if attempt == 0:
                try:
                    self.open_uri("spotify:")  # 打开电脑上的 Spotify,等它连上
                except OSError:
                    return None
                time.sleep(5)
        return None

    def play(self, query: str) -> str:
        query = query.strip()
        if not query:
            raise SpotifyError("要放什么?")
        if query in ("我喜欢的歌", "喜欢的歌", "我收藏的歌", "收藏的歌", "我喜欢的音乐"):
            status, data = self.api("GET", "/me/tracks", {"limit": 50})
            uris = [i["track"]["uri"] for i in data.get("items") or [] if i.get("track")] if status == 200 else []
            if not uris:
                return "你在 Spotify 还没有喜欢的歌。"
            return self._start({"uris": uris}, "你喜欢的歌", "spotify:collection:tracks")
        if query.startswith(("歌单", "我的歌单", "播放列表")):
            name = query.split("歌单", 1)[-1].replace("播放列表", "").strip()
            playlist = self._my_playlist(name) or (self._find(name, "playlist") if name else None)
            if playlist is None:
                return f"没找到叫“{name}”的歌单。"
            return self._start({"context_uri": playlist["uri"]}, f"歌单《{playlist.get('name', name)}》", playlist["uri"])
        track = self._find(query, "track")
        if track is None:
            return f"Spotify 上没找到“{query}”。"
        artists = "、".join(a.get("name", "") for a in track.get("artists") or [])
        return self._start({"uris": [track["uri"]]}, f"{artists}的《{track.get('name', query)}》", track["uri"])

    def now_playing(self) -> str:
        status, data = self.api("GET", "/me/player/currently-playing")
        if status == 204 or not data or not data.get("item"):
            return "Spotify 现在没有在放歌。"
        item = data["item"]
        artists = "、".join(a.get("name", "") for a in item.get("artists") or [])
        state = "正在放" if data.get("is_playing") else "暂停在"
        return f"{state}:{artists}《{item.get('name', '')}》。"
