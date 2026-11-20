"""字幕清理：去掉乱码字符，删除语音识别“幻听”出来的乱码条目，并记下每一处改动供核对。

乱码条目的特征是同一句里混进与整份字幕无关的文字（泰语字幕里夹着韩文、俄文、阿拉伯文），
或带着解码失败留下的替换字符 �。判断以整份文件为参照：先统计各文字系统的占比，
占比极低的（拉丁字母除外，人名、品牌、英文单词在各种语言里都常见）视为“外来文字”。
"""
from dataclasses import dataclass, replace
import re
import unicodedata

from .srt import Cue

REPLACEMENT = "�"
# 不可见、会干扰模型的字符：控制字符（保留换行和制表）、零宽空格、BOM、双向文本控制符。
# 零宽连接符 U+200C/U+200D 保留：表情和部分文字（如波斯语）靠它们正确显示。
_INVISIBLE = re.compile("[\x00-\x08\x0b-\x1f\x7f-\x9f​⁠﻿‪-‮⁦-⁩" + REPLACEMENT + "]")
_TAG = re.compile(r"<[^>]+>")
SCRIPT_NAMES = {"CJK": "汉字", "KANA": "日文假名", "HANGUL": "韩文", "CYRILLIC": "西里尔文（俄文等）",
                "ARABIC": "阿拉伯文", "HEBREW": "希伯来文", "GREEK": "希腊文", "THAI": "泰文", "LAO": "老挝文",
                "DEVANAGARI": "天城文（印地语等）", "KHMER": "高棉文", "MYANMAR": "缅甸文", "LATIN": "拉丁字母"}
RARE_SHARE = 0.03       # 在整份字幕里占比低于 3% 的文字系统算外来文字
JUNK_SHARE = 0.15       # 一条里外来文字占到 15% 以上就算乱码条目


@dataclass(frozen=True)
class Change:
    position: int       # 在原字幕里的顺序（1 起），原编号可能重复
    id: int
    action: str         # "fixed" 修正了字符 / "removed" 整条删除
    reason: str
    before: str
    after: str = ""


def script(ch):
    """字符所属的文字系统，取 Unicode 名称的第一个词，汉字与假名合并成两类。"""
    word = unicodedata.name(ch, "").split(" ")[0]
    return {"HIRAGANA": "KANA", "KATAKANA": "KANA"}.get(word, word)


def letters(text):
    return [script(ch) for ch in _TAG.sub("", text) if ch.isalpha()]


def census(cues):
    """整份字幕里各文字系统的字母数。"""
    counts = {}
    for cue in cues:
        for name in letters(cue.text):
            counts[name] = counts.get(name, 0) + 1
    return counts


def junk_reason(text, foreign, main):
    found = letters(text)
    if not found:
        return ""
    others = {name for name in found if name != main}
    if REPLACEMENT in text and others:
        # 识别模型吐出残缺字节时往往同时在乱拼外文；正常句子里偶尔一个 � 只去掉字符即可
        return "含解码失败的替换字符 �，且夹杂其他文字，疑似识别错误"
    strange = [name for name in found if name in foreign]
    if not strange:
        return ""
    if len(strange) / len(found) >= JUNK_SHARE or len(others) >= 2:
        names = "、".join(sorted({SCRIPT_NAMES.get(name, name) for name in strange}))
        return f"疑似识别错误：混入与全文无关的文字——{names}"
    return ""


def tidy(text):
    """去掉不可见字符；去掉后留下的连续空格合并成一个。"""
    cleaned = _INVISIBLE.sub("", text)
    if cleaned == text:
        return text
    return "\n".join(re.sub(r"[ \t]{2,}", " ", line).strip() for line in cleaned.split("\n")).strip()


def clean(cues: list[Cue]) -> tuple[list[Cue], list[Change]]:
    """返回清理后的字幕（重新编号，时间不变）和改动清单。"""
    counts = census(cues)
    total = sum(counts.values()) or 1
    main = max(counts, key=counts.get) if counts else ""
    foreign = {name for name, n in counts.items() if n / total < RARE_SHARE and name != "LATIN"}
    kept, changes = [], []
    for position, cue in enumerate(cues, 1):
        reason = junk_reason(cue.text, foreign, main)
        if reason:
            changes.append(Change(position, cue.id, "removed", reason, cue.text))
            continue
        text = tidy(cue.text)
        if not _TAG.sub("", text).strip():
            changes.append(Change(position, cue.id, "removed", "去掉乱码字符后没有内容", cue.text))
            continue
        if text != cue.text:
            changes.append(Change(position, cue.id, "fixed", "去掉乱码或不可见字符", cue.text, text))
        kept.append(replace(cue, id=len(kept) + 1, text=text))
    return kept, changes


def summary(changes):
    removed = sum(c.action == "removed" for c in changes)
    fixed = len(changes) - removed
    return f"删除 {removed} 条，修正 {fixed} 条" if changes else "无需清理"


def report(name, total, changes):
    """给人看的清理报告：每处改动写明位置、原因和前后内容，方便回到原字幕核对。"""
    lines = [f"{name}：共 {total} 条，{summary(changes)}", ""]
    for change in changes:
        head = f"第 {change.position} 条（编号 {change.id}）{'删除' if change.action == 'removed' else '修正'}：{change.reason}"
        lines.append(head)
        lines.append(f"  原文：{change.before}")
        if change.action == "fixed":
            lines.append(f"  改为：{change.after}")
        lines.append("")
    return "\n".join(lines)
