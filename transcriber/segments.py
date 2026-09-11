"""转录分段规划与模型输出解析。纯函数，不依赖 torch 或 ffmpeg。"""
from dataclasses import dataclass
import re

# 切点在目标位置前后这个范围内找静音，找不到就在目标处强制切分
TOLERANCE_SECONDS = 120.0
# 结束时间不晚于开始时间的字幕条目至少给这么长
MIN_CUE_MS = 300

CUE_RE = re.compile(r"\[(\d+(?:\.\d+)?)\]\[(S\d+)\](.*?)\[(\d+(?:\.\d+)?)\]", re.S)
TIMESTAMP_RE = re.compile(r"\[(\d+(?:\.\d+)?)\]")


@dataclass(frozen=True)
class Segment:
    """一个转录分段（秒）。forced 表示切点附近没有静音，可能切在一句话中间。"""
    start: float
    end: float
    forced: bool


@dataclass(frozen=True)
class Cue:
    """一个字幕条目（毫秒，全片时间）。"""
    start: int
    end: int
    text: str


def plan_segments(total_sec: float, silences, target_sec: float,
                  tolerance_sec: float = TOLERANCE_SECONDS) -> list[Segment]:
    """候选切点是各静音区间的中点；在 目标 ± 容差 内取离目标最近的，
    窗口内没有静音就在目标处强制切分。剩余不超过 目标 + 容差 时不再切。"""
    candidates = sorted(round((s + e) / 2, 3) for s, e in silences)
    segments, start = [], 0.0
    while total_sec - start > target_sec + tolerance_sec:
        aim = start + target_sec
        in_window = [p for p in candidates if aim - tolerance_sec <= p <= aim + tolerance_sec and p > start]
        if in_window:
            cut, forced = min(in_window, key=lambda p: abs(p - aim)), False
        else:
            cut, forced = aim, True
        segments.append(Segment(start, cut, forced))
        start = cut
    segments.append(Segment(start, total_sec, False))
    return segments


def parse_output(raw: str, offset_sec: float) -> list[Cue]:
    """解析模型输出 `[起始][Sxx] 正文[结束]`，时间加上分段偏移换成全片时间。
    不带说话人编号；格式不对或正文为空的内容被丢弃。"""
    cues = []
    for start, _speaker, body, end in CUE_RE.findall(raw):
        text = " ".join(body.split())
        if text:
            cues.append(Cue(round((float(start) + offset_sec) * 1000), round((float(end) + offset_sec) * 1000), text))
    return cues


def latest_timestamp(raw: str) -> float | None:
    """模型已输出的最后一个时间戳（相对分段开头，秒），用于估算转写进度。"""
    stamps = TIMESTAMP_RE.findall(raw)
    return float(stamps[-1]) if stamps else None


def finalize_cues(cues: list[Cue]) -> list[Cue]:
    """按开始时间排序；同时开始的条目（多人同时说话）合并为一条多行字幕；
    零长度补足最短时长；与下一条重叠时截到下一条开头。结果互不重叠。"""
    ordered = []
    for cue in sorted(cues, key=lambda c: c.start):
        if ordered and ordered[-1].start == cue.start:
            previous = ordered.pop()
            cue = Cue(cue.start, max(previous.end, cue.end), f"{previous.text}\n{cue.text}")
        ordered.append(cue)
    result = []
    for i, cue in enumerate(ordered):
        end = cue.end if cue.end > cue.start else cue.start + MIN_CUE_MS
        if i + 1 < len(ordered) and ordered[i + 1].start > cue.start:
            end = min(end, ordered[i + 1].start)
        result.append(Cue(cue.start, end, cue.text))
    return result
