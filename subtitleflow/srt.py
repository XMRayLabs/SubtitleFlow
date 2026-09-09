"""SRT parsing without third-party code or output license text."""
from dataclasses import dataclass
from pathlib import Path
import re


@dataclass(frozen=True)
class Cue:
    id: int
    start: int
    end: int
    text: str


STAMP = r"(\d{2,}):(\d{2}):(\d{2}),(\d{3})"
TIMELINE = re.compile(rf"^{STAMP}\s*-->\s*{STAMP}\s*$")


def milliseconds(parts):
    h, m, s, ms = map(int, parts)
    if m >= 60 or s >= 60:
        raise ValueError("分钟或秒超出范围")
    return ((h * 60 + m) * 60 + s) * 1000 + ms


def parse(text: str) -> list[Cue]:
    text = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        raise ValueError("SRT 文件为空")
    cues = []
    for position, block in enumerate(re.split(r"\n[ \t]*\n+", text), 1):
        lines = block.splitlines()
        if len(lines) < 3 or not lines[0].strip().isdigit():
            raise ValueError(f"第 {position} 条字幕缺少编号、时间轴或正文")
        match = TIMELINE.fullmatch(lines[1].strip())
        if not match:
            raise ValueError(f"第 {position} 条时间轴无效")
        start, end = milliseconds(match.groups()[:4]), milliseconds(match.groups()[4:])
        if start >= end:
            raise ValueError(f"第 {position} 条结束时间必须晚于开始时间")
        body = "\n".join(lines[2:]).strip()
        if not body:
            raise ValueError(f"第 {position} 条正文为空")
        if cues and start < cues[-1].start:
            raise ValueError(f"第 {position} 条开始时间倒序，请先修正原字幕")
        cues.append(Cue(int(lines[0]), start, end, body))
    return cues


def read(path: Path) -> list[Cue]:
    data = path.read_bytes()
    encodings = ("utf-16",) if data.startswith((b"\xff\xfe", b"\xfe\xff")) else ("utf-8-sig", "gb18030", "big5")
    for encoding in encodings:
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError:
            continue
        try:
            return parse(text)
        except ValueError as exc:
            raise ValueError(f"{path.name}: {exc}") from exc
    raise ValueError(f"{path.name}: 无法识别编码，请转换为 UTF-8")


def stamp(ms: int) -> str:
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def render(cues: list[Cue]) -> str:
    return "\n\n".join(f"{c.id}\n{stamp(c.start)} --> {stamp(c.end)}\n{c.text}" for c in cues) + "\n"


def write(path: Path, cues: list[Cue]):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(render(cues), encoding="utf-8")
    temporary.replace(path)
