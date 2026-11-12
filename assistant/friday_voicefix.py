"""语音识别出来的文字先整理一遍(不花钱):
  - 重复的词去掉:“下午下午三点” -> “下午三点”、“打开打开 Spotify” -> “打开 Spotify”;
  - 常听错的名字改回来:“星期无” -> “星期五”、“斯波蒂菲” -> “Spotify”、“迪斯科德” -> “Discord”……
  - 拿不准的(识别把握度低)交给调用的地方先问一句“你是说……吗?”。
只改中文;口译听外语时不动。
"""

from __future__ import annotations

import re

UNSURE_BELOW = 0.6  # 识别把握度低于这个:先问你是不是这个意思

# 常听错的 -> 正确的(只放这里会用到的名字,免得改错正常的话)
HOMOPHONES = {
    "星期无": "星期五", "星期舞": "星期五", "星期武": "星期五", "心期五": "星期五", "新期五": "星期五",
    "斯波蒂菲": "Spotify", "思波蒂菲": "Spotify", "斯坡蒂法": "Spotify", "斯波提菲": "Spotify", "四波地非": "Spotify",
    "迪斯科德": "Discord", "迪斯扣德": "Discord", "低斯扣": "Discord", "滴斯扣": "Discord",
    "优兔": "YouTube", "有图布": "YouTube", "油土鳖": "YouTube", "U 突": "YouTube",
    "斯蒂姆": "Steam", "斯迪姆": "Steam", "爱佩克斯": "Apex", "艾派克斯": "Apex", "阿佩克斯": "Apex",
    "刀他": "Dota", "多塔": "Dota", "推特": "Twitter", "微新": "微信", "威信": "微信",
    "谷歌日立": "Google 日历", "谷歌日里": "Google 日历", "塔波": "Tapo", "踏破": "Tapo",
}
REPEATED = re.compile(r"([一-鿿]{2,6})(?:\s*\1)+")  # 两到六个中文字连说了两次以上
REPEATED_WORD = re.compile(r"\b([A-Za-z]{2,})(?:\s+\1\b)+", re.I)
SPACES = re.compile(r"\s{2,}")


def clean(text: str | None, language: str = "zh-CN") -> str | None:
    if not text:
        return text
    if not str(language).lower().startswith("zh"):
        return text
    fixed = str(text)
    for wrong, right in HOMOPHONES.items():
        fixed = fixed.replace(wrong, right)
    fixed = REPEATED.sub(r"\1", fixed)
    fixed = REPEATED_WORD.sub(r"\1", fixed)
    fixed = SPACES.sub(" ", fixed).strip()
    return fixed or None


def unsure(confidence: float | None) -> bool:
    """识别把握度太低,要先问一下(没有把握度的识别方式就不问)。"""
    return confidence is not None and confidence < UNSURE_BELOW


YES = re.compile(r"对|是的?|没错|嗯|好|确定|就是|yes|yeah|ok", re.I)
NO = re.compile(r"不是|不对|错了|不要|取消|算了|no\b", re.I)


def answer(text: str) -> str:
    """回答“你是说……吗?”:yes / no / other(说的是别的,就当成新的一句)。"""
    said = str(text or "").strip()
    if len(said) <= 6 and NO.search(said):
        return "no"
    if len(said) <= 6 and YES.search(said):
        return "yes"
    return "other"
