"""会议 / 上课记录:说“开始记录”,星期五一直听(最长 3 小时),边听边把每 30 秒左右转成文字存起来(断电也不会全丢);
说“停止记录”后,请 Sonnet 写出重点、决定和待办,存成文件,窗口和手机都会收到。
说“把会议待办加进清单”就把待办加进你的待办清单。

文字记录和总结存在 friday_meetings 文件夹,也会自动收进“我的资料库”,以后可以问“上周开会决定了什么”。
转文字用免费的 Google 语音识别;总结一次大约 RM0.05~0.3(看多长)。
记录中不会被“星期五”唤醒词打断(会议里有人提到星期五也不会回应)。
"""

from __future__ import annotations

import datetime
import json
import os
import queue
import re
import threading
import time
from pathlib import Path
from typing import Callable

RATE = 16000
MIN_PIECE = 22  # 秒:到这么长后,遇到停顿就切一段
MAX_PIECE = 40  # 秒:再长就硬切(免费的语音识别一段最长 1 分钟左右)
MAX_HOURS = 3
QUIET_RMS = 350  # 比这个小当作停顿

START = re.compile(r"^(星期五)?[,，\s]*(?:(帮我)?开始(会议|上课|课堂|开会)?(记录|录音)(一下)?"
                   r"|(帮我)?(开始)?(记录|录)(一下)?(这个会|这场会|这堂课|这节课|会议|开会|上课|课堂|电话)(的)?(内容)?"
                   r"|开始(会议|上课|课堂)记录[:：\s]*(?P<t>[^。!！?？]{1,20}))[。!！]?$")
STOP = re.compile(r"^(星期五)?[,，\s]*(停止|结束|停掉|停)(会议|上课)?(记录|录音)(吧)?|^(星期五)?[,，\s]*(会议|开会|上课)(结束|完了|开完了)(了)?|^下课(了)?")
STATUS = re.compile(r"^(星期五)?[,，\s]*(记录了?多久(了)?|还在记录吗|在记录吗|录了多久(了)?)[?？]?$")
ADD_TODOS = re.compile(r"^(星期五)?[,，\s]*(把)?(刚才|上次)?(的)?(会议|开会|上课|课堂)?(的|里的)?待办(事项)?(全部|都)?(加进|加到|放进|放到)(我的)?(待办)?(清单)?(里)?[。!！]?$")
LAST = re.compile(r"^(星期五)?[,，\s]*(刚才|上次|最近)?(的)?(会议|开会|上课)(说了什么|讲了什么|总结|重点|的总结|的重点|记录)[?？]?$")

PROMPT = """下面是一段会议 / 上课的语音转文字(自动识别的,可能有错字、没有标点、几个人的话混在一起)。今天是 {today}。
请整理成:
- title:一个简短的标题(10 个字以内,没有给标题时用)
- summary:一句话说这次在讲什么
- points:重点(5~12 条,每条一句话,保留数字、日期、金额、名字)
- decisions:做了哪些决定(没有就空)
- todos:待办,每条写清楚做什么、谁做(没说就空字串)、什么时候(没说就空字串)
- questions:还没解决、要再确认的问题(没有就空)
识别错的字按意思猜回来;不要编造录音里没有的内容。"""

SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"}, "summary": {"type": "string"},
        "points": {"type": "array", "items": {"type": "string"}},
        "decisions": {"type": "array", "items": {"type": "string"}},
        "todos": {"type": "array", "items": {"type": "object", "properties": {
            "text": {"type": "string"}, "who": {"type": "string"}, "due": {"type": "string"}},
            "required": ["text", "who", "due"], "additionalProperties": False}},
        "questions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "summary", "points", "decisions", "todos", "questions"],
    "additionalProperties": False,
}


def rms(pcm: bytes) -> float:
    import array

    samples = array.array("h", pcm[: len(pcm) // 2 * 2])
    if not samples:
        return 0.0
    return (sum(s * s for s in samples[::4]) / max(1, len(samples[::4]))) ** 0.5


def clock_text(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def safe_title(text: str) -> str:
    return re.sub(r"[\\/:*?\"<>|\r\n\t]+", "", str(text or "")).strip()[:20]


class Mic:
    """sounddevice 的麦克风:start() 后一直收音,take() 拿走到现在为止收到的。"""

    def __init__(self) -> None:
        import sounddevice

        self.sd = sounddevice
        self.stream = None
        self.lock = threading.Lock()
        self.buffer = bytearray()

    def start(self) -> None:
        def keep(data, frames, time_info, status) -> None:
            with self.lock:
                self.buffer.extend(bytes(data))
        self.stream = self.sd.RawInputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=1600, callback=keep)
        self.stream.start()

    def take(self) -> bytes:
        with self.lock:
            data = bytes(self.buffer)
            self.buffer.clear()
        return data

    def stop(self) -> None:
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            finally:
                self.stream = None


class MeetingRecorder:
    def __init__(self, folder: str | os.PathLike, transcribe: Callable[[bytes], str | None],
                 summarize: Callable[[str, str], dict] | None = None, mic_factory: Callable[[], object] = Mic,
                 on_done: Callable[[dict], None] = lambda record: None, on_status: Callable[[str], None] = lambda text: None,
                 clock: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep,
                 now: Callable[[], datetime.datetime] = datetime.datetime.now) -> None:
        """transcribe(WAV) -> 文字;summarize(文字记录, 标题) -> {title, summary, points, decisions, todos, questions}。"""
        self.folder = Path(folder)
        self.transcribe = transcribe
        self.summarize = summarize
        self.mic_factory = mic_factory
        self.on_done, self.on_status = on_done, on_status
        self.clock, self.sleep, self.now = clock, sleep, now
        self.lock = threading.Lock()
        self.active = False
        self.finishing = False
        self.mic = None
        self.title = ""
        self.started = 0.0
        self.file: Path | None = None
        self.pieces: queue.Queue = queue.Queue()
        self.lines: list[str] = []
        self.failed_pieces = 0
        self.last: dict | None = self._load_last()

    # ---------- 开始、停止 ----------
    def start(self, title: str = "") -> str:
        with self.lock:
            if self.active:
                return f"已经在记录了({clock_text(self.clock() - self.started)})。说“停止记录”结束。"
            if self.finishing:
                return "上一段还在整理总结,等一下再开始新的记录。"
            try:
                mic = self.mic_factory()
                mic.start()
            except ImportError:
                return "录音组件还没装好:先在设定里打开一次“按住说话”,或者重开星期五让它自动安装。"
            except Exception as e:  # noqa: BLE001
                return f"麦克风打不开:{e}"
            self.mic, self.active, self.title = mic, True, safe_title(title)
            self.started = self.clock()
            self.lines, self.failed_pieces = [], 0
            self.folder.mkdir(parents=True, exist_ok=True)
            stamp = self.now().strftime("%Y-%m-%d %H%M")
            self.file = self.folder / f"{stamp} {self.title or '记录'}.txt"
            self.file.write_text(f"{self.title or '会议 / 上课记录'}({self.now():%Y-%m-%d %H:%M} 开始)\n\n", encoding="utf-8")
        threading.Thread(target=self._listen, daemon=True, name="friday-meeting-mic").start()
        threading.Thread(target=self._write, daemon=True, name="friday-meeting-text").start()
        return ("好,开始记录" + (f"“{self.title}”" if self.title else "") + "。我会一直听,边听边转成文字;"
                "结束时说“停止记录”,我整理出重点和待办。")

    def stop(self) -> str:
        with self.lock:
            if not self.active:
                return "现在没有在记录。"
            self.active = False
            self.finishing = True
        minutes = max(1, round((self.clock() - self.started) / 60))
        return f"好,停止记录(录了大约 {minutes} 分钟)。正在把最后一段转成文字、写总结,好了会告诉你。"

    def status(self) -> dict:
        return {"active": self.active, "finishing": self.finishing, "title": self.title,
                "seconds": int(self.clock() - self.started) if self.active else 0, "lines": len(self.lines),
                "last": self.last}

    # ---------- 收音、切段 ----------
    def _listen(self) -> None:
        pending = bytearray()
        piece_start = 0.0
        quiet = 0
        while True:
            self.sleep(0.5)
            stopped = not self.active or self.clock() - self.started > MAX_HOURS * 3600
            data = self.mic.take() if self.mic is not None else b""
            pending.extend(data)
            seconds = len(pending) / (RATE * 2)
            quiet = quiet + 1 if data and rms(data) < QUIET_RMS else 0
            if pending and (stopped or seconds >= MAX_PIECE or (seconds >= MIN_PIECE and quiet >= 1)):
                self.pieces.put((piece_start, bytes(pending)))
                piece_start += seconds
                pending.clear()
            if stopped:
                if self.active:  # 超过 3 小时:自己停
                    with self.lock:
                        self.active, self.finishing = False, True
                    self.on_status(f"已经录了 {MAX_HOURS} 小时,自动停止记录。")
                try:
                    self.mic.stop()
                except Exception:
                    pass
                self.pieces.put(None)
                return

    def _write(self) -> None:
        import friday_ptt

        while True:
            item = self.pieces.get()
            if item is None:
                break
            at, pcm = item
            if rms(pcm) < QUIET_RMS * 0.6:
                continue  # 整段都很安静
            try:
                text = self.transcribe(friday_ptt.to_wav(pcm))
            except Exception:  # noqa: BLE001
                text = None
                self.failed_pieces += 1
            if text:
                line = f"[{clock_text(at)}] {text}"
                self.lines.append(line)
                try:
                    with self.file.open("a", encoding="utf-8") as f:
                        f.write(line + "\n")
                except OSError:
                    pass
        self._finish()

    # ---------- 总结 ----------
    def _finish(self) -> None:
        transcript = "\n".join(self.lines)
        record = {"file": self.file.name if self.file else "", "title": self.title, "when": self.now().strftime("%Y-%m-%d %H:%M"),
                  "minutes": max(1, round((self.clock() - self.started) / 60)), "todos": [], "summary": "",
                  "failed": self.failed_pieces}
        try:
            if not transcript.strip():
                record["summary"] = "没有听到说话的内容(麦克风可能没收到声音)。"
            elif self.summarize is None:
                record["summary"] = "(没有总结)"
            else:
                data = self.summarize(transcript, self.title)
                record.update(self._save_summary(data))
        except Exception as e:  # noqa: BLE001
            record["summary"] = f"文字记录存好了,但总结没写成:{str(e)[:120] or type(e).__name__}。可以把文件拖进窗口叫我总结。"
        finally:
            with self.lock:
                self.finishing = False
                self.last = record
            self._save_last()
        self.on_done(record)

    def _save_summary(self, data: dict) -> dict:
        title = safe_title(self.title or data.get("title") or "记录")
        lines = [f"{title}({self.now():%Y-%m-%d})", "", data.get("summary", ""), ""]
        for label, key in (("重点", "points"), ("决定", "decisions"), ("还要确认", "questions")):
            items = [str(x) for x in data.get(key) or [] if str(x).strip()]
            if items:
                lines += [f"【{label}】"] + [f"- {x}" for x in items] + [""]
        todos = [t for t in data.get("todos") or [] if isinstance(t, dict) and str(t.get("text", "")).strip()]
        if todos:
            lines += ["【待办】"] + [f"- {t['text']}" + (f"({t['who']})" if t.get("who") else "")
                                    + (f" —— {t['due']}" if t.get("due") else "") for t in todos] + [""]
        lines.append(f"(完整文字记录:{self.file.name if self.file else ''})")
        summary_file = self.file.with_name(self.file.stem + " 总结.txt") if self.file else None
        if summary_file is not None:
            summary_file.write_text("\n".join(lines), encoding="utf-8")
        return {"title": title, "summary": "\n".join(lines[2:-1]).strip(), "todos": todos,
                "summary_file": summary_file.name if summary_file else ""}

    def _last_path(self) -> Path:
        return self.folder / "last.json"

    def _load_last(self) -> dict | None:
        try:
            data = json.loads(self._last_path().read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except (OSError, ValueError):
            return None

    def _save_last(self) -> None:
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            self._last_path().write_text(json.dumps(self.last, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass

    def add_todos(self, todos_store) -> str:
        last = self.last or {}
        todos = last.get("todos") or []
        if not todos:
            return "上次的记录里没有待办。"
        if last.get("todos_added"):
            return "上次记录的待办已经加进清单了。"
        for t in todos:
            todos_store.add(t["text"] + (f"({t['who']})" if t.get("who") else ""), t.get("due") or None)
        last["todos_added"] = True
        self._save_last()
        return f"加进待办清单了({len(todos)} 件):" + ";".join(t["text"] for t in todos[:6])

    def recent(self, limit: int = 6) -> list[dict]:
        if not self.folder.is_dir():
            return []
        files = sorted((p for p in self.folder.glob("*.txt") if not p.stem.endswith(" 总结")), reverse=True)[:limit]
        return [{"name": p.stem, "file": p.name, "summary": (p.with_name(p.stem + " 总结.txt").name
                                                             if p.with_name(p.stem + " 总结.txt").exists() else "")}
                for p in files]

    def handle(self, text: str, todos_store=None) -> str | None:
        text = str(text or "").strip()
        found = START.match(text)
        if found:
            return self.start(found.group("t") or "")
        if self.active and STOP.match(text):
            return self.stop()
        if STATUS.match(text):
            if self.active:
                return f"正在记录,已经 {clock_text(self.clock() - self.started)},转好了 {len(self.lines)} 段文字。"
            return "上一段还在整理总结。" if self.finishing else "现在没有在记录。"
        if ADD_TODOS.match(text) and todos_store is not None:
            return self.add_todos(todos_store)
        if LAST.match(text):
            if not self.last:
                return "还没有记录过。说“开始记录”就能记。"
            return f"上次记录({self.last.get('when', '')},{self.last.get('minutes', '?')} 分钟):\n{self.last.get('summary', '')}"
        return None


def summarizer(client, model: str, on_usage=None, today: Callable[[], str] = lambda: datetime.date.today().isoformat()):
    """用 Claude 写总结。"""
    def summarize(transcript: str, title: str) -> dict:
        response = client.messages.create(
            model=model, max_tokens=4096, system=PROMPT.format(today=today()),
            messages=[{"role": "user", "content": (f"标题:{title}\n" if title else "") + "【文字记录】\n" + transcript[:120000]}],
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
        )
        if on_usage is not None and getattr(response, "usage", None) is not None:
            try:
                on_usage(response.usage, model)
            except Exception:
                pass
        if getattr(response, "stop_reason", None) == "refusal":
            raise RuntimeError("Claude 不肯总结这段内容")
        text = next(b.text for b in response.content if getattr(b, "type", "") == "text")
        return json.loads(text)
    return summarize
