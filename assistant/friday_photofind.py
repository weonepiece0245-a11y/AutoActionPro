"""照片按时间、地点找:“去年在槟城拍的照片”“2025 年 3 月的照片”“上个月在云顶”。

- 在背景把“图片”文件夹(和 OneDrive 的图片)里的照片做一份目录:拍摄时间、GPS 位置(照片里的 EXIF);
- 位置相近的(大约 5 公里内)算同一个地方,每个地方只去 OpenStreetMap 问一次叫什么(中文、英文都记);
- 找的时候在目录里找,不问 Claude、不花钱;照片不会上传,只有“地方的经纬度”会送去查地名。
目录存在 friday_photo_index.json(只有路径、时间、位置,没有照片本身)。
"""

from __future__ import annotations

import datetime
import hashlib
import io
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

PHOTO_SUFFIXES = {".jpg", ".jpeg", ".heic", ".heif", ".png", ".webp", ".tif", ".tiff", ".dng"}
MAX_FILES = 60000
GRID = 0.05  # 度:大约 5 公里算同一个地方
NAME_PER_RUN = 80  # 一次最多查几个新地方的名字(一秒一个)
RESULT_LIMIT = 40
THUMB_SIZE = 240
SKIP_DIRS = {"$recycle.bin", "node_modules", "appdata", "cache", "thumbnails", ".thumbnails", "__pycache__"}
NAME_DATE = re.compile(r"(20\d{2}|19\d{2})[-_.]?(0[1-9]|1[0-2])[-_.]?(0[1-9]|[12]\d|3[01])(?:[-_. ]?([01]\d|2[0-3])[-_.]?([0-5]\d))?")
USER_AGENT = "friday-personal-assistant/1.0"

# 常用地名:中文 -> OpenStreetMap 上可能的名字
ALIASES = {
    "槟城": ["penang", "pulau pinang", "george town", "槟城", "檳城"], "吉隆坡": ["kuala lumpur", "吉隆坡"],
    "雪兰莪": ["selangor"], "八打灵": ["petaling jaya"], "梳邦": ["subang"], "布城": ["putrajaya"],
    "马六甲": ["melaka", "malacca", "马六甲"], "云顶": ["genting"], "怡保": ["ipoh"], "新山": ["johor bahru", "johor"],
    "柔佛": ["johor"], "兰卡威": ["langkawi"], "浮罗交怡": ["langkawi"], "金马伦": ["cameron"], "沙巴": ["sabah"],
    "亚庇": ["kota kinabalu"], "砂拉越": ["sarawak"], "古晋": ["kuching"], "关丹": ["kuantan"], "彭亨": ["pahang"],
    "霹雳": ["perak"], "吉打": ["kedah"], "登嘉楼": ["terengganu"], "热浪岛": ["redang"], "刁曼岛": ["tioman"],
    "波德申": ["port dickson"], "新加坡": ["singapore"], "曼谷": ["bangkok"], "普吉": ["phuket"], "合艾": ["hat yai"],
    "台北": ["taipei"], "台湾": ["taiwan"], "香港": ["hong kong"], "澳门": ["macau", "macao"], "东京": ["tokyo"],
    "大阪": ["osaka"], "京都": ["kyoto"], "北海道": ["hokkaido"], "日本": ["japan"], "首尔": ["seoul"], "韩国": ["korea"],
    "巴厘岛": ["bali"], "雅加达": ["jakarta"], "越南": ["vietnam", "viet nam"], "胡志明": ["ho chi minh"],
    "北京": ["beijing"], "上海": ["shanghai"], "广州": ["guangzhou"], "深圳": ["shenzhen"], "伦敦": ["london"],
    "澳洲": ["australia"], "悉尼": ["sydney"], "墨尔本": ["melbourne"], "马来西亚": ["malaysia"], "泰国": ["thailand"],
}
FILLER = re.compile(r"(帮我|给我|我|找一下|找找|找|看看|看一下|显示|列出|有没有|哪些|那些|那时候|时候|时|拍的|拍了|拍|"
                    r"的|照片|相片|图片|相簿|相册|photos?|pictures?|pics?|在|去|到|了|吗|呢|吧|一下|全部|所有|星期五|[,，。?？!！\s])", re.I)


def _fetch_json(url: str) -> object:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def default_folders() -> list[Path]:
    extra = [p for p in os.environ.get("FRIDAY_PHOTO_FOLDERS", "").split(";") if p.strip()]
    home = Path.home()
    found = []
    for path in [Path(p.strip()) for p in extra] + [home / "Pictures", home / "OneDrive" / "Pictures",
                                                     home / "OneDrive" / "图片", home / "OneDrive" / "圖片"]:
        try:
            if path.is_dir() and path.resolve() not in [f.resolve() for f in found]:
                found.append(path)
        except OSError:
            continue
    return found


def _ratio(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        try:
            return value[0] / value[1]
        except Exception:
            return 0.0


def _dms(values, ref) -> float | None:
    try:
        degrees = _ratio(values[0]) + _ratio(values[1]) / 60 + _ratio(values[2]) / 3600
    except (TypeError, IndexError):
        return None
    if str(ref or "").upper() in ("S", "W"):
        degrees = -degrees
    return round(degrees, 5)


def read_photo(path: Path) -> dict:
    """照片的拍摄时间('YYYY-MM-DD HH:MM')和 GPS(lat, lon);没有就从文件名、修改时间猜时间。"""
    taken, lat, lon = "", None, None
    try:
        from PIL import Image

        try:
            import pillow_heif  # 有装就能读 iPhone 的 HEIC

            pillow_heif.register_heif_opener()
        except Exception:
            pass
        with Image.open(path) as image:
            exif = image.getexif()
            sub = exif.get_ifd(0x8769) if exif else {}
            raw = (sub or {}).get(36867) or (sub or {}).get(36868) or exif.get(306)
            if raw:
                match = re.match(r"(\d{4}):(\d{2}):(\d{2})[ T](\d{2}):(\d{2})", str(raw))
                if match and match.group(1) != "0000":
                    taken = f"{match.group(1)}-{match.group(2)}-{match.group(3)} {match.group(4)}:{match.group(5)}"
            gps = exif.get_ifd(0x8825) if exif else {}
            if gps and 2 in gps and 4 in gps:
                lat, lon = _dms(gps[2], gps.get(1)), _dms(gps[4], gps.get(3))
                if lat is not None and lon is not None and abs(lat) < 0.001 and abs(lon) < 0.001:
                    lat = lon = None  # 0,0 是没有定位
    except Exception:
        pass
    if not taken:
        match = NAME_DATE.search(path.name)
        if match:
            hour, minute = match.group(4) or "12", match.group(5) or "00"
            taken = f"{match.group(1)}-{match.group(2)}-{match.group(3)} {hour}:{minute}"
    if not taken:
        try:
            taken = datetime.datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        except OSError:
            taken = ""
    return {"time": taken, "lat": lat, "lon": lon}


def cell(lat: float, lon: float) -> str:
    return f"{round(lat / GRID) * GRID:.2f},{round(lon / GRID) * GRID:.2f}"


# ---------- 听懂“什么时候” ----------
def _month_range(year: int, month: int) -> tuple[datetime.date, datetime.date]:
    start = datetime.date(year, month, 1)
    end = datetime.date(year + (month == 12), month % 12 + 1, 1)
    return start, end


CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
          "十一": 11, "十二": 12}


def _num(text: str) -> int | None:
    if text.isdigit():
        return int(text)
    return CN_NUM.get(text)


def parse_when(text: str, today: datetime.date) -> tuple[tuple[datetime.date, datetime.date] | None, str]:
    """“去年3月”“上个月”“2025年”“最近7天” -> ((开始, 结束不含), 剩下的字)。"""
    text = str(text or "")
    year_words = {"今年": 0, "去年": -1, "前年": -2, "大前年": -3}
    match = re.search(r"(大前年|前年|去年|今年|(\d{4})\s*年)\s*(?:的)?\s*(?:(\d{1,2}|十[一二]?|[一二两三四五六七八九])\s*月(?:份)?)?", text)
    if match:
        word = match.group(1)
        year = today.year + year_words[word] if word in year_words else int(match.group(2))
        month = _num(match.group(3)) if match.group(3) else None
        rest = text[:match.start()] + " " + text[match.end():]
        if month and 1 <= month <= 12:
            return _month_range(year, month), rest
        return (datetime.date(year, 1, 1), datetime.date(year + 1, 1, 1)), rest
    match = re.search(r"(\d{4})[-/.](\d{1,2})(?:[-/.](\d{1,2}))?", text)
    if match:
        year, month = int(match.group(1)), int(match.group(2))
        rest = text[:match.start()] + " " + text[match.end():]
        if 1 <= month <= 12:
            if match.group(3):
                try:
                    day = datetime.date(year, month, int(match.group(3)))
                    return (day, day + datetime.timedelta(days=1)), rest
                except ValueError:
                    pass
            return _month_range(year, month), rest
    match = re.search(r"(这个月|本月|上个月|上月|上上个月)", text)
    if match:
        back = {"这个月": 0, "本月": 0, "上个月": 1, "上月": 1, "上上个月": 2}[match.group(1)]
        year, month = today.year, today.month - back
        while month < 1:
            year, month = year - 1, month + 12
        return _month_range(year, month), text.replace(match.group(1), " ", 1)
    match = re.search(r"(这个?星期|这周|本周|上个?星期|上周|上个?礼拜)", text)
    if match:
        monday = today - datetime.timedelta(days=today.weekday())
        if match.group(1).startswith("上"):
            monday -= datetime.timedelta(days=7)
        return (monday, monday + datetime.timedelta(days=7)), text.replace(match.group(1), " ", 1)
    match = re.search(r"(今天|昨天|前天)", text)
    if match:
        day = today - datetime.timedelta(days={"今天": 0, "昨天": 1, "前天": 2}[match.group(1)])
        return (day, day + datetime.timedelta(days=1)), text.replace(match.group(1), " ", 1)
    match = re.search(r"(?:最近|过去|这)\s*(\d{1,3}|[一二两三四五六七八九十])\s*(天|个?星期|周|个月)", text)
    if match:
        n = _num(match.group(1)) or 1
        days = n * {"天": 1, "星期": 7, "个星期": 7, "周": 7, "个月": 30}[match.group(2)]
        return (today - datetime.timedelta(days=days - 1), today + datetime.timedelta(days=1)), \
            text[:match.start()] + " " + text[match.end():]
    match = re.search(r"(\d{1,2}|十[一二]?|[一二两三四五六七八九])\s*月(?:份)?", text)
    if match:
        month = _num(match.group(1))
        if month and 1 <= month <= 12:
            year = today.year if month <= today.month else today.year - 1  # 还没到的月份:说的是去年
            return _month_range(year, month), text[:match.start()] + " " + text[match.end():]
    return None, text


def place_words(rest: str) -> list[str]:
    """时间拿掉之后剩下的字 -> 要找的地名(含别名)。"""
    word = FILLER.sub(" ", rest or "").strip()
    word = " ".join(word.split())
    if len(word) < 2 and not re.search(r"[一-鿿]", word):
        return []
    if not word:
        return []
    words = [word.lower()]
    for key, names in ALIASES.items():
        if key in word:
            words += names
    return list(dict.fromkeys(words))


class PhotoIndex:
    def __init__(self, path: str | os.PathLike, folders: Callable[[], list[Path]] = default_folders,
                 fetch: Callable[[str], object] = _fetch_json, sleep=time.sleep,
                 today: Callable[[], datetime.date] = datetime.date.today) -> None:
        self.path = Path(path)
        self.folders, self.fetch, self.sleep, self.today = folders, fetch, sleep, today
        self.lock = threading.Lock()
        self.busy = False
        self.thumbs: dict[str, bytes] = {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        self.items: dict[str, dict] = data.get("items") if isinstance(data.get("items"), dict) else {}
        self.places: dict[str, dict] = data.get("places") if isinstance(data.get("places"), dict) else {}
        self.scanned = str(data.get("scanned", ""))

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"scanned": self.scanned, "items": self.items, "places": self.places},
                                      ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            pass

    @staticmethod
    def photo_id(path: str) -> str:
        return hashlib.sha1(os.path.normcase(path).encode("utf-8", "surrogatepass")).hexdigest()[:16]

    # ---------- 做目录 ----------
    def scan(self) -> dict:
        """看一遍照片文件夹:新的、改过的才读;删掉的拿掉。返回 {photos, new, places}。"""
        if self.busy:
            return {"busy": True}
        self.busy = True
        try:
            seen, new = set(), 0
            by_path = {item["path"]: (pid, item) for pid, item in self.items.items()}
            for folder in self.folders():
                for root, dirs, files in os.walk(folder):
                    dirs[:] = [d for d in dirs if not d.startswith(".") and d.lower() not in SKIP_DIRS]
                    for name in files:
                        if Path(name).suffix.lower() not in PHOTO_SUFFIXES or len(seen) >= MAX_FILES:
                            continue
                        full = os.path.join(root, name)
                        try:
                            stat = os.stat(full)
                        except OSError:
                            continue
                        if stat.st_size < 20 * 1024:  # 太小的多半是图标、缩图
                            continue
                        pid, old = by_path.get(full, (self.photo_id(full), None))
                        seen.add(pid)
                        if old is not None and old.get("mtime") == int(stat.st_mtime) and old.get("size") == stat.st_size:
                            continue
                        info = read_photo(Path(full))
                        entry = {"path": full, "mtime": int(stat.st_mtime), "size": stat.st_size, "time": info["time"]}
                        if info["lat"] is not None:
                            entry["cell"] = cell(info["lat"], info["lon"])
                        with self.lock:
                            self.items[pid] = entry
                        new += 1
                        if new % 500 == 0:
                            self._save()
            with self.lock:
                for pid in [p for p in self.items if p not in seen]:
                    self.items.pop(pid, None)
                self.scanned = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
            named = self.name_places()
            self._save()
            return {"photos": len(self.items), "new": new, "places": named}
        finally:
            self.busy = False

    def name_places(self) -> int:
        """还不知道名字的地方:问 OpenStreetMap(中文、英文各一次;一秒最多一次)。"""
        cells = {item["cell"] for item in self.items.values() if item.get("cell")}
        todo = [c for c in cells if c not in self.places][:NAME_PER_RUN]
        named = 0
        for key in todo:
            lat, lon = (float(x) for x in key.split(","))
            names: list[str] = []
            label = ""
            for language in ("zh", "en"):
                url = "https://nominatim.openstreetmap.org/reverse?" + urllib.parse.urlencode(
                    {"lat": f"{lat:.2f}", "lon": f"{lon:.2f}", "format": "jsonv2", "zoom": 12,
                     "accept-language": language})
                try:
                    data = self.fetch(url)
                except Exception:
                    data = None
                self.sleep(1.1)
                address = (data or {}).get("address") if isinstance(data, dict) else None
                if not address:
                    continue
                parts = [address.get(k) for k in ("suburb", "town", "city", "municipality", "county", "state_district",
                                                  "state", "island", "country") if address.get(k)]
                names += [str(p) for p in parts]
                if language == "zh" or not label:
                    city = address.get("city") or address.get("town") or address.get("county") or address.get("state")
                    label = ", ".join(str(x) for x in (city, address.get("country")) if x) or label
            if names:
                self.places[key] = {"label": label, "names": list(dict.fromkeys(n.lower() for n in names))}
                named += 1
            elif len(self.places) < 5000:
                self.places[key] = {"label": "", "names": []}  # 海上、查不到:记下来不再问
        return named

    # ---------- 找 ----------
    def search(self, start: datetime.date | None = None, end: datetime.date | None = None,
               places: list[str] | None = None, limit: int = RESULT_LIMIT) -> dict:
        lo = start.isoformat() if start else ""
        hi = end.isoformat() if end else "9999"
        wanted = [p.lower() for p in places or [] if p.strip()]
        cells = None
        if wanted:
            cells = {key for key, place in self.places.items()
                     if any(w in name or (len(name) >= 3 and name in w) for w in wanted for name in place.get("names", []))}
        with self.lock:
            hits = [(pid, item) for pid, item in self.items.items()
                    if lo <= item.get("time", "") < hi and (cells is None or item.get("cell") in cells)]
        hits.sort(key=lambda pair: pair[1].get("time", ""))
        shown = hits if len(hits) <= limit else [hits[round(i * (len(hits) - 1) / (limit - 1))] for i in range(limit)]
        return {"total": len(hits), "items": [{"id": pid, "time": item.get("time", ""), "name": Path(item["path"]).name,
                                               "place": self.places.get(item.get("cell", ""), {}).get("label", "")}
                                              for pid, item in shown],
                "known_place": cells is None or bool(cells),
                "first": hits[0][1]["time"] if hits else "", "last": hits[-1][1]["time"] if hits else ""}

    def ask(self, text: str) -> tuple[dict, str]:
        """一句话 -> (结果, 怎么理解的)。"""
        when, rest = parse_when(text, self.today())
        places = place_words(rest)
        result = self.search(*(when or (None, None)), places=places)
        parts = []
        if when:
            last = when[1] - datetime.timedelta(days=1)
            parts.append(when[0].isoformat() if when[0] == last else f"{when[0]} ~ {last}")
        if places:
            parts.append(places[0])
        return result, " · ".join(parts)

    def describe(self, result: dict, understood: str) -> str:
        if not self.items:
            return "照片目录还没做好(开着星期五会在背景慢慢做,第一次可能要几分钟)。"
        if not result["known_place"]:
            return f"照片里没有在「{understood}」拍的(有 GPS 的照片才知道地点;手机相机要开定位)。"
        if not result["total"]:
            return f"没找到{('「' + understood + '」') if understood else ''}的照片。"
        span = result["first"][:10] if result["first"][:10] == result["last"][:10] else \
            f"{result['first'][:10]} 到 {result['last'][:10]}"
        more = f",下面先列 {len(result['items'])} 张" if result["total"] > len(result["items"]) else ""
        return f"找到 {result['total']} 张{('「' + understood + '」') if understood else ''}的照片({span}){more},点一下就打开。"

    # ---------- 缩图、打开 ----------
    def file_of(self, photo_id: str) -> str | None:
        item = self.items.get(str(photo_id or ""))
        return item["path"] if item and os.path.isfile(item["path"]) else None

    def thumb(self, photo_id: str) -> bytes | None:
        if photo_id in self.thumbs:
            return self.thumbs[photo_id]
        path = self.file_of(photo_id)
        if path is None:
            return None
        try:
            from PIL import Image, ImageOps

            with Image.open(path) as image:
                image.draft("RGB", (THUMB_SIZE * 2, THUMB_SIZE * 2))
                image = ImageOps.exif_transpose(image)
                image.thumbnail((THUMB_SIZE, THUMB_SIZE))
                buffer = io.BytesIO()
                image.convert("RGB").save(buffer, "JPEG", quality=78)
        except Exception:
            return None
        data = buffer.getvalue()
        if len(self.thumbs) > 300:
            self.thumbs.clear()
        self.thumbs[photo_id] = data
        return data

    def stats(self) -> dict:
        with_gps = sum(1 for item in self.items.values() if item.get("cell"))
        return {"photos": len(self.items), "with_place": with_gps, "places": sum(1 for p in self.places.values() if p.get("label")),
                "scanned": self.scanned, "busy": self.busy, "folders": [str(f) for f in self.folders()]}
