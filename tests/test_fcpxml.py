import json
from fractions import Fraction
from pathlib import Path
import tempfile
import threading
import unittest
import xml.etree.ElementTree as ET
from subtitleflow.merge import join_text
from subtitleflow.srt import Cue, render as srt_render
from subtitleflow.fcpxml import render, ExportOptions, FPS
from subtitleflow.jobs import Job, JobOptions
from subtitleflow.api import APIConfig


class MarkupTests(unittest.TestCase):
    def test_bold_sentence(self):
        parts = ["Pagkakalim na mga palyari ng Diyos,", "kaya pinabalaan po ang lahat ng mga tao",
                 "at inapalalaan sa kanila lahat", "na ang papalalaan ng mga pangyayon",
                 "ay hindi natatangin bukasyon", "ng isang bukit o bububo ng mga tao."]
        self.assertEqual(join_text(["<b>" + p + "</b>" for p in parts]), "<b>" + " ".join(parts) + "</b>")

    def test_chinese_and_nested(self):
        self.assertEqual(join_text(["<b>你</b>", "<b>好。</b>"]), "<b>你好。</b>")
        self.assertEqual(join_text(["<b><i>Hello</i></b>", "<b><i>world.</i></b>"]), "<b><i>Hello world.</i></b>")

    def test_mixed_style_not_extended(self):
        self.assertEqual(join_text(["<b>Hello</b>", "world."]), "<b>Hello</b> world.")
        self.assertEqual(join_text(["<b>A</b>", "<i>B</i>"]), "<b>A</b> <i>B</i>")


def time_value(value):
    return Fraction(value.removesuffix("s"))


class FCPXMLTests(unittest.TestCase):
    def test_styling_escaping_and_multiline(self):
        root = ET.fromstring(render([Cue(1, 1000, 2300, "<b>A &amp; B</b>\n<i>中文</i>")], "项目"))
        self.assertEqual(root.tag, "fcpxml")
        title = root.find(".//title")
        self.assertEqual("".join(title.find("text").itertext()), "A & B\n中文")
        self.assertEqual(time_value(title.get("offset")), 1)
        self.assertTrue(any(s.get("bold") == "1" for s in title.findall("text-style-def/text-style")))
        self.assertTrue(any(s.get("italic") == "1" for s in title.findall("text-style-def/text-style")))
        self.assertIsNone(root.find(".//asset"))

    def test_wrap_30_characters_keeps_one_title_and_srt(self):
        for length in (29, 30, 31, 60, 61):
            with self.subTest(length=length):
                cue = Cue(7, 1000, 5000, "<b>" + "字" * length + "</b>")
                before = srt_render([cue])
                root = ET.fromstring(render([cue], "test"))
                titles = root.findall(".//title")
                self.assertEqual(len(titles), 1)
                text = "".join(titles[0].find("text").itertext())
                self.assertEqual(text, "\n".join("字" * min(30, length-i) for i in range(0,length,30)))
                self.assertEqual(time_value(titles[0].get("offset")), 1)
                self.assertEqual(time_value(titles[0].get("duration")), 4)
                self.assertEqual(srt_render([cue]), before)

    def test_wrap_across_styles_and_preserve_existing_newline(self):
        cue = Cue(1, 0, 1000, "<b>" + "甲"*29 + "</b><i>乙丙</i><br>丁")
        root = ET.fromstring(render([cue], "test"))
        title = root.find(".//title")
        self.assertEqual("".join(title.find("text").itertext()), "甲"*29 + "乙\n丙\n丁")
        spans = title.find("text").findall("text-style")
        self.assertEqual(spans[1].text, "乙\n丙")
        styles = {d.get("id"):d.find("text-style") for d in title.findall("text-style-def")}
        self.assertEqual(styles[spans[1].get("ref")].get("italic"), "1")

    def test_frame_alignment_and_overlaps(self):
        for fps in FPS:
            root = ET.fromstring(render([Cue(1, 1001, 2002, "a"), Cue(2, 1500, 1501, "b")], "test", ExportOptions(fps)))
            titles = root.findall(".//title")
            self.assertNotEqual(titles[0].get("lane"), titles[1].get("lane"))
            for title in titles:
                for attr in ("offset", "duration"):
                    self.assertEqual((time_value(title.get(attr)) * FPS[fps]).denominator, 1)
                self.assertGreater(time_value(title.get("duration")), 0)
            self.assertGreaterEqual(time_value(root.find(".//sequence").get("duration")), 2)

    def test_standalone_batch_no_api(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "a.srt"
            source.write_text(srt_render([Cue(1, 0, 1000, "Hello")]), encoding="utf-8")
            out = Job([source], root, JobOptions(mode="fcpxml"), APIConfig("", ""), threading.Event()).run()
            self.assertTrue((out / "转换后的fcpxml/a.fcpxml").exists())
            self.assertFalse((out / "合并后的srt").exists())
            self.assertFalse((out / "翻译后的srt").exists())

    def test_merge_and_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "a.srt"
            source.write_text(srt_render([Cue(1, 0, 1000, "<b>Hello</b>"), Cue(2, 1000, 2000, "<b>world.</b>")]), encoding="utf-8")
            out = Job([source], root, JobOptions(mode="merge", export_fcpxml=True), APIConfig("", ""), threading.Event()).run()
            self.assertIn("<b>Hello world.</b>", (out / "合并后的srt/a.srt").read_text(encoding="utf-8"))
            self.assertEqual(len(ET.parse(out / "转换后的fcpxml/a.fcpxml").findall(".//title")), 1)


if __name__ == "__main__":
    unittest.main()
