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
