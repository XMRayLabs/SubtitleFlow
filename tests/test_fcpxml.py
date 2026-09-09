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
            self.assertTrue((out / "fcpxml/a.fcpxml").exists())
            self.assertFalse((out / "merged").exists())
            self.assertFalse((out / "translated").exists())

    def test_merge_and_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "a.srt"
            source.write_text(srt_render([Cue(1, 0, 1000, "<b>Hello</b>"), Cue(2, 1000, 2000, "<b>world.</b>")]), encoding="utf-8")
            out = Job([source], root, JobOptions(mode="merge", export_fcpxml=True), APIConfig("", ""), threading.Event()).run()
            self.assertIn("<b>Hello world.</b>", (out / "merged/a.srt").read_text(encoding="utf-8"))
            self.assertEqual(len(ET.parse(out / "fcpxml/a.fcpxml").findall(".//title")), 1)


if __name__ == "__main__":
    unittest.main()
