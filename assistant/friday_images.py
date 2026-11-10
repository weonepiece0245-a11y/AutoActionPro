"""手机发来的照片:缩小成适合 Claude 看的大小(省钱、也比较快),变成图片内容块。"""

from __future__ import annotations

import base64
import io

MAX_SIDE = 1568  # Claude 看图最合适的长边;再大也会被缩小,只是多花钱
MAX_RAW = 5 * 1024 * 1024
PLACEHOLDER = "[这里原本有一张照片,已经看过;要再看请对方重新发]"


def _sniff(data: bytes) -> str | None:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def prepare(data: bytes) -> dict | None:
    """照片 -> Claude 的 image 内容块;认不出来返回 None。"""
    try:
        from PIL import Image, ImageOps

        with Image.open(io.BytesIO(data)) as image:
            image = ImageOps.exif_transpose(image)  # 手机竖着拍的照片转正
            image.thumbnail((MAX_SIDE, MAX_SIDE))
            if image.mode not in ("RGB", "L"):
                image = image.convert("RGB")
            out = io.BytesIO()
            image.save(out, "JPEG", quality=85)
        return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                            "data": base64.b64encode(out.getvalue()).decode("ascii")}}
    except ImportError:
        pass
    except Exception:
        return None
    kind = _sniff(data)
    if kind is None or len(data) > MAX_RAW:
        return None
    return {"type": "image", "source": {"type": "base64", "media_type": kind,
                                        "data": base64.b64encode(data).decode("ascii")}}


# ======================== 窗口里贴进来、拖进来的文件 ========================

TEXT_SUFFIXES = (".txt", ".md", ".csv", ".json", ".log", ".py", ".js", ".ts", ".html", ".htm", ".xml", ".yaml",
                 ".yml", ".ini", ".cfg", ".bat", ".ps1", ".sql", ".srt")
MAX_TEXT_CHARS = 200_000


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def docx_text(data: bytes) -> str:
    """Word 文件(.docx)里的文字:每一段一行。"""
    import re
    import zipfile
    from html import unescape

    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        xml = archive.read("word/document.xml").decode("utf-8", errors="replace")
    xml = re.sub(r"</w:p>", "\n", xml)
    xml = re.sub(r"<w:tab/>", "\t", xml)
    return unescape(re.sub(r"<[^>]+>", "", xml)).strip()


def _text_document(name: str, text: str) -> dict:
    if len(text) > MAX_TEXT_CHARS:
        text = text[:MAX_TEXT_CHARS] + "\n……(后面太长,省略了)"
    return {"type": "document", "title": name[:100],
            "source": {"type": "text", "media_type": "text/plain", "data": text or "(空的)"}}


def file_block(name: str, mime: str, data: bytes) -> dict | str:
    """一个文件 -> Claude 的内容块;不支持的返回一句说明。"""
    lower = name.lower()
    if mime.startswith("image/") or lower.endswith((".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".heic")):
        return prepare(data) or f"{name}:这张图片打不开"
    if mime == "application/pdf" or lower.endswith(".pdf"):
        return {"type": "document", "title": name[:100],
                "source": {"type": "base64", "media_type": "application/pdf", "data": base64.b64encode(data).decode()}}
    if lower.endswith(".docx"):
        try:
            return _text_document(name, docx_text(data))
        except Exception:
            return f"{name}:这个 Word 文件打不开"
    if mime.startswith("text/") or lower.endswith(TEXT_SUFFIXES):
        return _text_document(name, _decode(data))
    return f"{name}:这种文件还看不了(可以看图片、PDF、Word .docx 和文字文件)"


def file_blocks(files: list[tuple[str, str, bytes]]) -> tuple[list[dict], list[str]]:
    """[(文件名, 类型, 内容)] -> (内容块, 看不了的说明)。"""
    blocks, problems = [], []
    for name, mime, data in files:
        block = file_block(name, mime, data)
        (problems if isinstance(block, str) else blocks).append(block)
    return blocks, problems
