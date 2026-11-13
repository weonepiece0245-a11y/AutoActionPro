"""读给我听:网页文章、PDF、Word、文字文件、复制的文字,用星期五的声音一段一段读出来(不花钱)。

读的时候可以说“暂停”“继续”“跳过这段”“上一段”“读快一点”“读慢一点”“别读了”。
只在电脑的窗口里读(声音从电脑出来)。读的内容只是读出来,不会交给 Claude。
"""

from __future__ import annotations

import html
import html.parser
import re
import threading
import urllib.request
from pathlib import Path

CHUNK_CHARS = 280
MAX_CHARS = 200_000
MAX_DOWNLOAD = 5_000_000
RATES = (0.8, 0.9, 1.0, 1.15, 1.3, 1.5, 1.7)
SKIP_TAGS = {"script", "style", "noscript", "nav", "footer", "header", "aside", "form", "button", "svg", "figure",
             "iframe", "select", "template"}
BLOCK_TAGS = {"p", "h1", "h2", "h3", "h4", "li", "blockquote", "pre", "div", "section", "article", "br", "tr", "td"}
SENTENCE_END = re.compile(r"(?<=[。!?！？;；…])|(?<=[.!?])\s+")


class _TextParser(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.in_title = False
        self.skip = 0
        self.parts: list[str] = []
        self.article: list[str] | None = None
        self.article_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in SKIP_TAGS:
            self.skip += 1
        if tag == "title":
            self.in_title = True
        if tag == "article":
            self.article_depth += 1
            if self.article is None:
                self.article = []
        if tag in BLOCK_TAGS:
            self._add("\n")

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS and self.skip:
            self.skip -= 1
        if tag == "title":
            self.in_title = False
        if tag == "article" and self.article_depth:
            self.article_depth -= 1
        if tag in BLOCK_TAGS:
            self._add("\n")

    def handle_data(self, data):
        if self.in_title:
            self.title += data
            return
        if not self.skip:
            self._add(data)

    def _add(self, text: str) -> None:
        self.parts.append(text)
        if self.article_depth and self.article is not None:
            self.article.append(text)


def paragraphs(text: str) -> list[str]:
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return [line for line in lines if len(line) >= 2]


def extract_html(page: str) -> tuple[str, list[str]]:
    """网页 -> (标题, 正文段落)。有 <article> 就只读文章部分;去掉很短的菜单、按钮文字。"""
    parser = _TextParser()
    parser.feed(page)
    body = "".join(parser.article) if parser.article and len("".join(parser.article)) > 300 else "".join(parser.parts)
    paras = paragraphs(body)
    long_ones = [p for p in paras if len(p) >= 25 or re.search(r"[。.!?!?]$", p)]
    return html.unescape(" ".join(parser.title.split())), long_ones or paras


def chunks(paras: list[str], size: int = CHUNK_CHARS) -> list[str]:
    """把段落切成一段一段(每段一两百字,在句子结尾切),方便一段一段读。"""
    out: list[str] = []
    for para in paras:
        if len(para) <= size:
            out.append(para)
            continue
        current = ""
        for sentence in SENTENCE_END.split(para):
            if not sentence:
                continue
            if current and len(current) + len(sentence) > size:
                out.append(current.strip())
                current = ""
            while len(sentence) > size * 2:  # 一句太长:硬切
                out.append(sentence[:size])
                sentence = sentence[size:]
            current += sentence
        if current.strip():
            out.append(current.strip())
    return [c for c in out if c.strip()]


def load_url(url: str) -> tuple[str, list[str]]:
    import friday_watch

    if not url.lower().startswith(("http://", "https://")) or not friday_watch._public_host(url):
        raise ValueError("只能读公开网站的网页")
    try:
        with friday_watch.open_public(url) as response:  # 跳转也检查,不会被带去内部地址
            data = response.read(MAX_DOWNLOAD)
            charset = response.headers.get_content_charset() or ""
            kind = response.headers.get_content_type()
    except OSError as e:
        raise ValueError(f"打不开这个网页:{e}") from None
    if kind == "application/pdf":
        return pdf_text(data, url.rsplit("/", 1)[-1])
    text = data.decode(charset or "utf-8", errors="replace")
    if not charset:
        found = re.search(r'<meta[^>]+charset=["\']?([\w-]+)', text[:3000], re.I)
        if found and found.group(1).lower() not in ("utf-8", "utf8"):
            try:
                text = data.decode(found.group(1), errors="replace")
            except LookupError:
                pass
    title, paras = extract_html(text)
    if not paras:
        raise ValueError("这个网页读不到文章内容(可能要登录,或者是用程序画出来的)")
    return title or url, paras


def pdf_text(data: bytes, name: str) -> tuple[str, list[str]]:
    try:
        import io

        import pypdf
    except ImportError:
        raise ValueError("读 PDF 要先装 pypdf(检查更新时会自动装)") from None
    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
        text = "\n".join((page.extract_text() or "") for page in reader.pages[:300])
    except Exception:
        raise ValueError("这个 PDF 打不开(可能有密码或者坏了)") from None
    # PDF 每行都断开:把不是句子结尾的换行接起来
    text = re.sub(r"(?<![。!?!?.:;:;])\n(?=\S)", "", text) if re.search(r"[一-鿿]", text) else re.sub(r"(?<![.!?:;])\n(?=\S)", " ", text)
    paras = paragraphs(text)
    if not paras:
        raise ValueError("这个 PDF 里没有文字(可能是扫描的图片)")
    return name, paras


def load_file(path: str | Path) -> tuple[str, list[str]]:
    import friday_images
    import friday_security

    path = Path(path)
    if friday_security.is_secret_path(path):
        raise ValueError("这是钥匙、密码或登录资料,星期五不读")
    if not path.is_file():
        raise ValueError(f"找不到文件 {path}")
    data = path.read_bytes()[: 50_000_000]
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return pdf_text(data, path.name)
    if suffix == ".docx":
        return path.stem, paragraphs(friday_images.docx_text(data))
    if suffix in (".html", ".htm"):
        title, paras = extract_html(friday_images._decode(data))
        return title or path.stem, paras
    if suffix in (".txt", ".md", ".log", ".csv", ".srt"):
        return path.stem, paragraphs(friday_images._decode(data))
    raise ValueError("这种文件读不了(可以读网页、PDF、Word .docx、文字文件)")


class Reader:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.id = 0
        self.title = ""
        self.parts: list[str] = []
        self.pos = 0
        self.state = "idle"  # idle / reading / paused / done
        self.rate = 1.0
        self.turn = 0
        self.on_change = lambda event: None  # 告诉窗口(开始、暂停、停止、改速度)
        self.new_turn = lambda: 0  # 要一个新的朗读编号(跟回答的朗读用同一套)

    @property
    def active(self) -> bool:
        return self.state in ("reading", "paused")

    def _event(self, **extra) -> None:
        self.on_change({"type": "reader", "id": self.id, "state": self.state, "index": self.pos,
                        "total": len(self.parts), "turn": self.turn, "rate": self.rate, "title": self.title, **extra})

    def start(self, title: str, paras: list[str]) -> str:
        text_len = sum(len(p) for p in paras)
        if not paras or not text_len:
            raise ValueError("没有可以读的文字")
        cut = []
        total = 0
        for para in paras:
            if total >= MAX_CHARS:
                break
            cut.append(para[: MAX_CHARS - total])
            total += len(cut[-1])
        with self.lock:
            self.id += 1
            self.title = (title or "这篇文字")[:80]
            self.parts = chunks(cut)
            self.pos = 0
            self.state = "reading"
            self.turn = self.new_turn()
        self._event()
        minutes = max(1, round(total / 260))
        more = "(太长了,只读前面一部分)" if text_len > MAX_CHARS else ""
        return (f"开始读《{self.title}》,一共 {len(self.parts)} 段,大约 {minutes} 分钟{more}。"
                "可以说“暂停”“继续”“跳过这段”“读快一点”“别读了”。")

    def pause(self) -> str:
        if self.state != "reading":
            return "现在没有在读。"
        self.state = "paused"
        self._event()
        return f"暂停了(读到第 {self.pos + 1}/{len(self.parts)} 段)。说“继续”接着读。"

    def resume(self) -> str:
        if not self.parts or self.state == "idle":
            return "现在没有要读的东西。"
        if self.state == "done":
            return "已经读完了。说“从头再读”可以重读。"
        self.state = "reading"
        self.turn = self.new_turn()
        self._event()
        return f"好,从第 {self.pos + 1} 段接着读。"

    def stop(self) -> str:
        if not self.active:
            return "现在没有在读。"
        self.state = "idle"
        self._event()
        return "好,不读了。"

    def move(self, step: int) -> str:
        if not self.parts or self.state == "idle":
            return "现在没有在读。"
        self.pos = max(0, min(len(self.parts) - 1, self.pos + step))
        self.state = "reading"
        self.turn = self.new_turn()
        self._event()
        return f"好,读第 {self.pos + 1} 段。"

    def restart(self) -> str:
        if not self.parts:
            return "现在没有要读的东西。"
        self.pos = 0
        self.state = "reading"
        self.turn = self.new_turn()
        self._event()
        return "好,从头再读。"

    def speed(self, faster: bool) -> str:
        index = min(range(len(RATES)), key=lambda i: abs(RATES[i] - self.rate))
        index = min(len(RATES) - 1, index + 1) if faster else max(0, index - 1)
        self.rate = RATES[index]
        if self.active:
            self._event(kind="rate")
        return f"好,速度改成 {self.rate:g} 倍。"

    def status(self) -> str:
        if not self.active:
            return "现在没有在读。"
        word = "在读" if self.state == "reading" else "暂停在"
        return f"{word}《{self.title}》第 {self.pos + 1}/{len(self.parts)} 段。"

    def serve(self, data: dict, synthesize, store) -> dict:
        """窗口要下一段的声音:{id, index, playing} -> {url, index, total} / {done} / {stop}。"""
        if str(data.get("action")) == "finished":
            if int(data.get("id") or 0) == self.id and self.state == "reading":
                self.state = "done"
                self._event()
            return {"ok": True}
        try:
            wanted_id, index = int(data.get("id") or 0), int(data.get("index") or 0)
        except (TypeError, ValueError):
            return {"stop": True}
        if wanted_id != self.id or self.state != "reading":
            return {"stop": True}
        if data.get("playing") is not None:
            try:
                self.pos = max(0, min(len(self.parts) - 1, int(data["playing"])))
            except (TypeError, ValueError):
                pass
        if index >= len(self.parts):
            return {"done": True, "total": len(self.parts)}
        audio = synthesize(self.parts[index]) if synthesize else None
        if not audio:
            return {"skip": True, "index": index, "total": len(self.parts)}
        return {"url": f"/audio/{store.put(audio)}.mp3", "index": index, "total": len(self.parts)}
