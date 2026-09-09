"""Original FCPXML 1.7 writer; uses FCP's installed Basic Title, no bundled template."""
from dataclasses import dataclass
from fractions import Fraction
from html.parser import HTMLParser
from pathlib import Path
import re
import xml.etree.ElementTree as ET

FPS = {"23.976": Fraction(24000, 1001), "24": Fraction(24), "25": Fraction(25),
       "29.97": Fraction(30000, 1001), "30": Fraction(30), "50": Fraction(50),
       "59.94": Fraction(60000, 1001), "60": Fraction(60)}


@dataclass(frozen=True)
class ExportOptions:
    fps: str = "25"
    width: int = 1920
    height: int = 1080

    def validate(self):
        if self.fps not in FPS or not 16 <= self.width <= 16384 or not 16 <= self.height <= 16384:
            raise ValueError("FCPXML 帧率或画面尺寸无效")


class SubtitleText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.bold = self.italic = 0
        self.runs = []

    def handle_starttag(self, tag, attrs):
        if tag in ("b", "strong"):
            self.bold += 1
        elif tag in ("i", "em"):
            self.italic += 1
        elif tag == "br":
            self.handle_data("\n")

    def handle_endtag(self, tag):
        if tag in ("b", "strong"):
            self.bold = max(0, self.bold - 1)
        elif tag in ("i", "em"):
            self.italic = max(0, self.italic - 1)

    def handle_data(self, value):
        if value:
            style = (self.bold > 0, self.italic > 0)
            if self.runs and self.runs[-1][1] == style:
                self.runs[-1] = (self.runs[-1][0] + value, style)
            else:
                self.runs.append((value, style))


def rational(value):
    value = Fraction(value)
    return f"{value.numerator}s" if value.denominator == 1 else f"{value.numerator}/{value.denominator}s"


def frame(ms, fps):
    value = Fraction(ms, 1000) * fps
    return (value.numerator * 2 + value.denominator) // (2 * value.denominator)


def render(cues, name, options=ExportOptions()):
    options.validate()
    if not cues:
        raise ValueError("没有可导出的字幕")
    fps = FPS[options.fps]
    timings = [(frame(c.start, fps), max(frame(c.start, fps) + 1, frame(c.end, fps))) for c in cues]
    duration = rational(Fraction(max(end for _, end in timings), 1) / fps)
    root = ET.Element("fcpxml", version="1.7")
    resources = ET.SubElement(root, "resources")
    ET.SubElement(resources, "format", id="r1", frameDuration=rational(1 / fps),
                  width=str(options.width), height=str(options.height), colorSpace="1-1-1 (Rec. 709)")
    ET.SubElement(resources, "effect", id="r2", name="Basic Title",
                  uid=".../Titles.localized/Bumper:Opener/Basic Title/Basic Title.moti")
    library = ET.SubElement(root, "library")
    event = ET.SubElement(library, "event", name="字幕导入")
    project = ET.SubElement(event, "project", name=name)
    sequence = ET.SubElement(project, "sequence", format="r1", duration=duration, tcStart="0s", tcFormat="NDF")
    spine = ET.SubElement(sequence, "spine")
    gap = ET.SubElement(spine, "gap", name="字幕时间线", offset="0s", start="0s", duration=duration)
    lane_ends = []
    for index, (cue, (start, end)) in enumerate(zip(cues, timings), 1):
        # Preserve overlaps using separate connected lanes.
        lane = next((i for i, last in enumerate(lane_ends) if last <= start), len(lane_ends))
        if lane == len(lane_ends):
            lane_ends.append(end)
        else:
            lane_ends[lane] = end
        title = ET.SubElement(gap, "title", ref="r2", name=f"字幕 {index}", lane=str(lane + 1),
                              offset=rational(Fraction(start, 1) / fps), start="0s",
                              duration=rational(Fraction(end - start, 1) / fps), role="titles")
        parser = SubtitleText()
        parser.feed(cue.text)
        parser.close()
        text = ET.SubElement(title, "text")
        styles = {}
        for value, style in parser.runs:
            if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", value):
                raise ValueError(f"第 {index} 条字幕包含 XML 不允许的控制字符")
            if style not in styles:
                styles[style] = f"ts{index}_{len(styles)}"
            ET.SubElement(text, "text-style", ref=styles[style]).text = value
        for (bold, italic), style_id in styles.items():
            definition = ET.SubElement(title, "text-style-def", id=style_id)
            ET.SubElement(definition, "text-style", font="Helvetica", fontSize="48",
                          fontColor="1 1 1 1", alignment="center", bold=str(int(bold)), italic=str(int(italic)))
        ET.SubElement(title, "adjust-transform", position="0 -40")
    # Do not pretty-print mixed text: indentation would alter subtitle content.
    return '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n' + ET.tostring(root, encoding="unicode") + "\n"


def write(path: Path, cues, options=ExportOptions()):
    content = render(cues, path.stem, options)
    temporary = path.with_suffix(".fcpxml.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)
