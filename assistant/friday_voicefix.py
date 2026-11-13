"""语音识别出来的文字先整理一遍(不花钱):
  - 重复的词去掉:“下午下午三点” -> “下午三点”、“打开打开 Spotify” -> “打开 Spotify”;
  - 常听错的名字改回来:“星期无” -> “星期五”、“斯波蒂菲” -> “Spotify”、“迪斯科德” -> “Discord”……
  - 拿不准的(识别把握度低)交给调用的地方先问一句“你是说……吗?”。
只改中文;口译听外语时不动。
"""

from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path

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
# 吉隆坡常夹的英文 / 马来文词,被听成中文字的(只放不会和正常中文混淆的)
MALAYSIA = {
    "格拉布": "Grab", "格拉伯": "Grab", "米德瓦利": "Mid Valley", "米德威利": "Mid Valley", "帕维连": "Pavilion",
    "肖皮": "Shopee", "拉扎达": "Lazada", "福德潘达": "foodpanda",
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
    for wrong, right in list(HOMOPHONES.items()) + list(MALAYSIA.items()) + VOCAB.pairs():
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


# ======================== 你自己的词库(听错改正 + 常用词) ========================

MAX_FIXES = 200
MAX_WORDS = 100
ADD_FIX = re.compile(r"^(星期五)?[,，\s]*以后(你)?(听到|听见|听成)[“\"']?(?P<h>[^“”\"']{1,12}?)[”\"']?(就|的话就?)?(是|改成|当成|当作|写成)[“\"']?(?P<m>[^“”\"']{1,30}?)[”\"']?[。!！]?$")


def mixed_enabled() -> bool:
    """中英马混着说:拿不准时再用英文识别试一次。"""
    return os.environ.get("FRIDAY_STT_MIXED", "on") != "off"


class Vocab:
    """friday_vocab.json:{"fixes": [{"heard": 听错的, "meant": 正确的}], "words": [常用词]}。"""

    def __init__(self, path: str | os.PathLike | None = None) -> None:
        self.path = Path(path) if path else None
        self.lock = threading.Lock()
        self.fixes: list[dict] = []
        self.words: list[str] = []
        if self.path is not None:
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
            if isinstance(data, dict):
                self.fixes = [f for f in data.get("fixes") or [] if isinstance(f, dict)
                              and isinstance(f.get("heard"), str) and isinstance(f.get("meant"), str)][:MAX_FIXES]
                self.words = [w for w in data.get("words") or [] if isinstance(w, str)][:MAX_WORDS]

    def _save(self) -> None:
        if self.path is None:
            return
        temp = self.path.with_name(self.path.name + ".tmp")
        temp.write_text(json.dumps({"fixes": self.fixes, "words": self.words}, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(temp, self.path)

    def pairs(self) -> list[tuple[str, str]]:
        return [(f["heard"], f["meant"]) for f in self.fixes]

    @staticmethod
    def _clean(text: str, limit: int) -> str:
        text = " ".join(str(text or "").split())
        if not text or len(text) > limit or any(c in text for c in "\n\r<>{}"):
            raise ValueError(f"要 1~{limit} 个字")
        return text

    def add(self, heard: str, meant: str) -> bool:
        heard, meant = self._clean(heard, 12), self._clean(meant, 30)
        if heard == meant or heard in meant and len(heard) < 2:
            raise ValueError("听错的字和正确的词要不一样")
        with self.lock:
            for fix in self.fixes:
                if fix["heard"] == heard:
                    if fix["meant"] == meant:
                        return False
                    fix["meant"] = meant
                    self._save()
                    return True
            self.fixes = (self.fixes + [{"heard": heard, "meant": meant}])[-MAX_FIXES:]
            self._save()
        return True

    def remove(self, heard: str) -> bool:
        with self.lock:
            before = len(self.fixes)
            self.fixes = [f for f in self.fixes if f["heard"] != heard]
            if len(self.fixes) != before:
                self._save()
                return True
        return False

    def set_words(self, words: list[str]) -> list[str]:
        cleaned = []
        for word in words:
            word = " ".join(str(word or "").split())[:30]
            if word and word not in cleaned and not any(c in word for c in "<>{}"):
                cleaned.append(word)
        with self.lock:
            self.words = cleaned[:MAX_WORDS]
            self._save()
        return self.words

    def known(self) -> list[str]:
        """挑候选时优先的词:常用词 + 改正后的词。"""
        return self.words + [f["meant"] for f in self.fixes]

    def handle(self, text: str) -> str | None:
        """“以后听到米谷就是 Mid Valley”:直接记下,不问 Claude。"""
        found = ADD_FIX.match(str(text or "").strip())
        if not found:
            return None
        heard, meant = found.group("h").strip(), found.group("m").strip()
        try:
            self.add(heard, meant)
        except ValueError as e:
            return f"没记下:{e}"
        return f"好,以后听到“{heard}”我就当成“{meant}”。"


VOCAB = Vocab()


def use_vocab(path: str | os.PathLike) -> Vocab:
    """启动时:读你自己的词库(friday_vocab.json)。"""
    global VOCAB
    VOCAB = Vocab(path)
    return VOCAB


def pick(alternatives: list[tuple[str, float | None]]) -> tuple[str | None, float | None]:
    """识别给了好几个候选:第一个没有你的常用词、后面的(前三个里)有,就选后面那个。"""
    if not alternatives:
        return None, None
    known = [w.lower() for w in VOCAB.known() + list(MALAYSIA.values()) if w]
    first_text, first_conf = alternatives[0]

    def hits(text: str) -> int:
        low = (text or "").lower()
        return sum(1 for w in known if w in low)
    best = max(alternatives[:3], key=lambda item: hits(item[0]))
    if hits(best[0]) > hits(first_text):
        return best[0], first_conf
    return first_text, first_conf


def mostly_latin(text: str) -> bool:
    letters = [c for c in str(text or "") if not c.isspace()]
    return bool(letters) and sum(c.isascii() for c in letters) / len(letters) >= 0.8
