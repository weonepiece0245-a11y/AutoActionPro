"""本机 Whisper 语音识别:在你自己的电脑上听(不上传、不花钱),华语、English、Bahasa Melayu 混着说也认得比较准。

- 用开源的 faster-whisper(OpenAI Whisper 的加速版);有 NVIDIA 显卡用显卡跑(一句话不到一秒),
  没有就用 CPU 跑(慢一点,一句话一两秒);
- 第一次用要安装(齿轮设定 → 声音 → 语音识别 选“本机 Whisper”):装程序库几百 MB、下载模型约 1.6GB;
- 会把你的常用词(“以后听到 X 就是 Y”、教过的名字)和一句中英马混着的例句当提示,认得更准;
- 出问题(没装好、显卡驱动不对)会自动退回 Google 识别,不会听不到。
"""

from __future__ import annotations

import math
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable

DEFAULT_MODEL = "large-v3-turbo"
CPU_MODEL = "small"  # 没有显卡:用小一点的,才不会太慢
SAMPLE_RATE = 16000
PROMPT = ("以下是马来西亚华人日常说话,华语夹 English 和 Bahasa Melayu,例如:星期五,帮我 check 一下明天的 meeting,"
          "然后 tolong set 一个 reminder 三点 call 我。我在 Mid Valley 吃 nasi lemak,Grab 回家。")
PACKAGES = ["faster-whisper"]
GPU_PACKAGES = ["nvidia-cublas-cu12", "nvidia-cudnn-cu12==9.*"]

_model = None
_model_info = ""
_lock = threading.Lock()
_failed = ""  # 上次载入失败的原因(就不一直重试)


def model_name() -> str:
    return os.environ.get("FRIDAY_WHISPER_MODEL", "").strip() or DEFAULT_MODEL


def models_dir() -> Path:
    custom = os.environ.get("FRIDAY_WHISPER_DIR", "").strip()
    return Path(custom) if custom else Path(__file__).resolve().parent / "whisper_models"


def has_nvidia(run=subprocess.run) -> bool:
    try:
        proc = run(["nvidia-smi", "-L"], capture_output=True, timeout=10,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return proc.returncode == 0 and b"GPU" in (proc.stdout or b"")
    except (OSError, subprocess.SubprocessError):
        return False


def installed() -> bool:
    try:
        import faster_whisper  # noqa: F401
    except Exception:
        return False
    return True


def _python() -> str:
    python = sys.executable
    if python.lower().endswith("pythonw.exe") and os.path.exists(python[:-5] + ".exe"):
        python = python[:-5] + ".exe"
    return python


def _cuda_dlls() -> None:
    """pip 装的 NVIDIA 程序库(cuBLAS、cuDNN)的 DLL 文件夹告诉 Windows,显卡才用得了。"""
    if os.name != "nt":
        return
    for module in ("nvidia.cublas", "nvidia.cudnn"):
        try:
            package = __import__(module, fromlist=["__path__"])
            for base in getattr(package, "__path__", []):
                for sub in ("bin", "lib"):
                    folder = os.path.join(base, sub)
                    if os.path.isdir(folder):
                        os.add_dll_directory(folder)
                        os.environ["PATH"] = folder + os.pathsep + os.environ.get("PATH", "")
        except Exception:
            continue


def install(on_status: Callable[[str], None] = lambda s: None, run=subprocess.run, gpu: bool | None = None) -> str:
    """装 faster-whisper(有 NVIDIA 显卡再装显卡用的程序库),再下载模型、试听一次。返回给你看的话;失败抛 RuntimeError。"""
    global _failed
    gpu = has_nvidia(run) if gpu is None else gpu
    packages = PACKAGES + (GPU_PACKAGES if gpu else [])
    on_status("🎙 安装本机语音识别的程序库(几百 MB,要几分钟)……")
    proc = run([_python(), "-m", "pip", "install", "-q", "--disable-pip-version-check", *packages],
               capture_output=True, timeout=1800, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if proc.returncode != 0:
        tail = (proc.stderr or b"").decode("utf-8", "replace").strip()[-400:]
        raise RuntimeError(f"程序库没装成功(可能是 Python 版本太新还没有对应的版本):{tail}")
    _failed = ""
    on_status("🎙 下载语音模型(约 1.6GB,第一次比较久)……")
    load(force=True)
    return f"本机语音识别装好了({_model_info})。以后说话都在你电脑上认,不上传。"


def load(force: bool = False):
    """载入模型(第一次会下载);有显卡先试显卡,不行就用 CPU。"""
    global _model, _model_info, _failed
    with _lock:
        if _model is not None and not force:
            return _model
        if _failed and not force:
            raise RuntimeError(_failed)
        try:
            from faster_whisper import WhisperModel
        except Exception as e:
            _failed = f"还没装本机语音识别({type(e).__name__})"
            raise RuntimeError(_failed) from e
        _cuda_dlls()
        folder = str(models_dir())
        errors = []
        name = model_name()
        for device, compute, size in (("cuda", "float16", name), ("cpu", "int8", CPU_MODEL if name == DEFAULT_MODEL else name)):
            try:
                _model = WhisperModel(size, device=device, compute_type=compute, download_root=folder)
                _model_info = f"{size},{'显卡' if device == 'cuda' else 'CPU'}"
                _failed = ""
                return _model
            except Exception as e:  # noqa: BLE001  显卡驱动 / 程序库不对:换 CPU
                errors.append(f"{device}:{type(e).__name__}: {e}"[:200])
        _failed = "本机语音识别载入不了:" + ";".join(errors)
        raise RuntimeError(_failed)


def info() -> str:
    return _model_info


def prompt(words: list[str]) -> str:
    extra = "、".join(w for w in words[:40] if w)
    return PROMPT + (f"常用词:{extra}。" if extra else "")


def whisper_language(language: str) -> str | None:
    """Google 的语言代码 -> Whisper 的;华语(会夹英文、马来文)让它自己判断。"""
    code = str(language or "").lower().split("-")[0]
    if code in ("", "zh", "cmn", "yue"):
        return None
    return code


def transcribe(pcm: bytes, language: str = "zh-CN", words: list[str] | None = None) -> list[tuple[str, float | None]]:
    """16kHz 16-bit 单声道 PCM -> [(文字, 把握度 0~1)];没听到 []。载入不了抛 RuntimeError。"""
    import numpy as np

    model = load()
    audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    if audio.size < SAMPLE_RATE // 4:
        return []
    lang = whisper_language(language)
    segments, details = model.transcribe(
        audio, language=lang, beam_size=5, vad_filter=True, condition_on_previous_text=False,
        initial_prompt=prompt(words or []) if lang is None else None, without_timestamps=True)
    parts, logprobs, silent = [], [], []
    for seg in segments:
        text = str(getattr(seg, "text", "")).strip()
        if not text:
            continue
        parts.append(text)
        logprobs.append(float(getattr(seg, "avg_logprob", -1.0)))
        silent.append(float(getattr(seg, "no_speech_prob", 0.0)))
    if not parts or (silent and min(silent) > 0.8):
        return []
    text = _join(parts)
    if text.strip().startswith(("以下是马来西亚", "以下是馬來西亞")) or "常用词:" in text:
        return []  # 没声音时偶尔把提示的开头念回来(你真的说了例句里的话不算)
    confidence = max(0.0, min(1.0, math.exp(sum(logprobs) / len(logprobs))))
    if lang is None and getattr(details, "language", "") in ("zh", "yue"):
        text = to_simplified(text)
    return [(text, round(confidence, 3))]


def _join(parts: list[str]) -> str:
    out = ""
    for part in parts:
        if out and out[-1].isascii() and part[:1].isascii():
            out += " "
        out += part
    return out


# 常见繁体字 -> 简体(Whisper 偶尔用繁体写中文;只放常用的,不装别的程序库)
_T2S = str.maketrans(
    "們這個來說時會對後還沒點開關麼為們與東車馬見問間聽讀寫記帳單錢買賣發電話號碼條種樣將從經過現實實現體驗驗證"
    "給讓幫請誰變運動頭題麗愛歡謝謝鐘錶業網絡應該雲飛機場門開關燈櫃夠嗎哪裡裏邊遠近劃畫報導學習還錄員區號碼係圖書館",
    "们这个来说时会对后还没点开关么为们与东车马见问间听读写记帐单钱买卖发电话号码条种样将从经过现实实现体验验证"
    "给让帮请谁变运动头题丽爱欢谢谢钟表业网络应该云飞机场门开关灯柜够吗哪里里边远近划画报导学习还录员区号码系图书馆")


_T2S.update(str.maketrans({"議": "议", "確": "确", "帳": "账", "認": "认", "識": "识", "語": "语", "設": "设", "計": "计",
                           "訊": "讯", "號": "号", "麼": "么", "廳": "厅", "嗎": "吗", "灣": "湾", "邊": "边", "傳": "传",
                           "準": "准", "備": "备", "錯": "错", "試": "试", "數": "数", "據": "据", "選": "选", "擇": "择",
                           "儲": "储", "幣": "币", "價": "价", "貴": "贵", "買": "买", "專": "专", "線": "线", "聯": "联"}))


def to_simplified(text: str) -> str:
    return text.translate(_T2S)
