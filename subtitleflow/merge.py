from dataclasses import dataclass
import math
import re
from .srt import Cue


@dataclass(frozen=True)
class MergeOptions:
    target: float = 10.0
    silence: float = 3.0
    tolerance: float = 25.0

    def validate(self):
        if not all(math.isfinite(v) for v in (self.target, self.silence, self.tolerance)):
            raise ValueError("合并参数必须为有限数值")
        if self.target <= 0 or self.silence <= 0 or not 0 <= self.tolerance <= 200:
            raise ValueError("目标时长和静音阈值必须大于 0，容差范围为 0–200%")


def sentence_end(text: str) -> bool:
    clean = re.sub(r"<[^>]*>", "", text).strip().rstrip('”’"\'」』】）)] ')
    if re.search(r"[。！？!?]$", clean):
        return True
    if not clean.endswith("."):
        return False
    if re.search(r"\b(?:Mr|Mrs|Ms|Dr|Prof|Sr|Jr|St|vs|etc|e\.g|i\.e)\.$", clean, re.I):
        return False
    if re.search(r"(?:\b[A-Za-z]\.)+$", clean):
        return False
    return True


def join_text(parts: list[str]) -> str:
    result = ""
    for part in parts:
        part = part.strip()
        if not result:
            result = part
        elif re.search(r"[\u3400-\u9fff]$", re.sub(r"<[^>]+>", "", result)) and re.match(r"[\u3400-\u9fff，。！？、；：]", re.sub(r"<[^>]+>", "", part)):
            result += part
        else:
            result += " " + part
    # Coalesce identical adjacent style spans, including nested b/i/u wrappers.
    previous = None
    while previous != result:
        previous = result
        result = re.sub(r"</(b|i|u)>(\s*)<\1>", r"\2", result, flags=re.I)
        # Nested identical wrappers are handled from the outside in.
        result = re.sub(r"</(b|i|u)></(b|i|u)>(\s*)<\2><\1>", r"\3", result, flags=re.I)
    return result


def merge(cues: list[Cue], options: MergeOptions, boundaries: set[int] | None = None) -> list[Cue]:
    """boundaries are zero-based original cue positions ending sentences."""
    options.validate()
    if not cues:
        return []
    target = round(options.target * 1000)
    limit = math.ceil(options.target * (1 + options.tolerance / 100)) * 1000
    silence = round(options.silence * 1000)
    ends = boundaries if boundaries is not None else {i for i, c in enumerate(cues) if sentence_end(c.text)}
    result = []
    start = 0
    while start < len(cues):
        # A silence boundary always wins, even when AI labels a longer sentence.
        segment_end = start
        covered_end = cues[start].end
        while segment_end + 1 < len(cues):
            if cues[segment_end + 1].start - covered_end >= silence:
                break
            segment_end += 1
            covered_end = max(covered_end, cues[segment_end].end)
        cursor = start
        while cursor <= segment_end:
            end = cursor
            group_end = cues[cursor].end
            while end < segment_end:
                duration = group_end - cues[cursor].start
                if duration >= target and end in ends:
                    break
                next_end = max(group_end, cues[end + 1].end)
                # Sentence preference cannot bypass the duration budget.
                # An existing long cue is retained intact, never extended further.
                if next_end - cues[cursor].start > limit:
                    break
                future_sentence = any(j in ends for j in range(end + 1, segment_end + 1))
                gap = cues[end + 1].start - group_end
                if not future_sentence and duration >= target and gap > 0:
                    break
                end += 1
                group_end = max(group_end, cues[end].end)
            group = cues[cursor:end + 1]
            result.append(Cue(len(result) + 1, group[0].start, max(c.end for c in group), join_text([c.text for c in group])))
            cursor = end + 1
        start = segment_end + 1
    return result
