"""字幕清理：去掉乱码字符、删除识别错误的乱码条目，并列出每一处改动。"""
from pathlib import Path
import tempfile
import threading
import unittest

from subtitleflow.api import APIConfig
from subtitleflow.clean import clean
from subtitleflow.jobs import Job, JobOptions
from subtitleflow.srt import Cue, read, render


def cues(*texts):
    return [Cue(i + 1, i * 1000, i * 1000 + 900, t) for i, t in enumerate(texts)]


THAI = ["<b>พี่น้องชายอยู่ทุกท่าน</b>", "<b>เราได้มาเจอกันที่นี่ วันนี้</b>", "<b>ตอนรักตาพี่น้องทุกท่าน</b>",
        "<b>ขอบคุณมากครับ ไป sure</b>", "<b>สวัสดีครับ Milan</b>"] * 4


class CharacterTests(unittest.TestCase):
    def test_replacement_and_invisible_characters_are_removed(self):
        result, changes = clean(cues("Hello​ wor\x07ld", "Fine﻿"))
        self.assertEqual([c.text for c in result], ["Hello world", "Fine"])
        self.assertEqual([(c.position, c.action) for c in changes], [(1, "fixed"), (2, "fixed")])

    def test_joiners_and_line_breaks_survive(self):
        text = "👩‍💻 line one\nline two"
        result, changes = clean(cues(text))
        self.assertEqual(result[0].text, text)
        self.assertEqual(changes, [])

    def test_cue_left_empty_is_removed(self):
        result, changes = clean(cues("ok", "��", "<b>​</b>"))
        self.assertEqual([c.text for c in result], ["ok"])
        self.assertEqual([(c.position, c.action) for c in changes], [(2, "removed"), (3, "removed")])


class JunkCueTests(unittest.TestCase):
    def run_clean(self, *junk):
        return clean(cues(*THAI, *junk))

    def test_hallucinated_mixed_script_cues_are_removed(self):
        junk = ["<b>ขอบคุณฟ الزูนสวัสดี</b>", "<b>เรจะความหายในประตесь</b>", "<b>ท�� çocukพ SANDS</b>",
                "<b>ца</b>", "<b>เหมือน腐มมันต้องอยู่าพย sour 괜찮</b>"]
        result, changes = self.run_clean(*junk)
        self.assertEqual(len(result), len(THAI))
        removed = [c for c in changes if c.action == "removed"]
        self.assertEqual([c.before for c in removed], junk)
        self.assertTrue(all(c.reason for c in removed))

    def test_real_speech_with_a_small_glitch_is_kept(self):
        line = "<b>พระออนุ่ง ทุกคน ต้องนอนนะคะ พระออนุ่ง ทุกคน ต้องนอนорон</b>"
        result, _ = self.run_clean(line)
        self.assertIn(line, [c.text for c in result])

    def test_english_words_in_thai_are_not_junk(self):
        result, changes = self.run_clean()
        self.assertEqual(len(result), len(THAI))
        self.assertEqual(changes, [])

    def test_two_main_scripts_are_both_normal(self):
        mixed = ["今日はいい天気ですね", "東京へ行きます", "ありがとうございます"] * 5
        result, changes = clean(cues(*mixed))
        self.assertEqual(changes, [])

    def test_output_is_renumbered_and_keeps_timing(self):
        result, _ = clean(cues(THAI[0], "<b>ца</b>", *THAI[1:]))
        self.assertEqual([c.id for c in result], list(range(1, len(THAI) + 1)))
        self.assertEqual(result[1].start, 2000)            # 删掉的条目留下空档，不挪动后面的时间


class CleanJobTests(unittest.TestCase):
    def test_clean_mode_writes_srt_and_report_without_api(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "a.srt"
            source.write_text(render(cues(*THAI, "<b>ца</b>", "<b>สวัสดีครับ�</b>")), encoding="utf-8")
            events = []
            output = Job([source], root / "out", JobOptions(mode="clean"), APIConfig("", "", ""),
                         threading.Event(), lambda *e: events.append(e)).run()
            cleaned = read(output / "清理后的srt" / "a.srt")
            self.assertEqual(len(cleaned), len(THAI) + 1)
            self.assertEqual(cleaned[-1].text, "<b>สวัสดีครับ</b>")
            report = (output / "清理报告" / "a.txt").read_text(encoding="utf-8")
            self.assertIn("删除 1 条", report)
            self.assertIn("修正 1 条", report)
            self.assertIn("ца", report)
            self.assertIn(("file", 0, "已完成", "删除 1 条，修正 1 条"), events)
            self.assertFalse((output / "翻译后的srt").exists())


class CleanModeWindowTests(unittest.TestCase):
    def setUp(self):
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        QApplication.instance() or QApplication([])
        from test_gui_pages import WindowTestCase
        helper = WindowTestCase()
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        from subtitleflow import gui
        self.window = gui.Window()
        self.addCleanup(self.window.deleteLater)

    def test_mode_needs_no_api_and_hides_export(self):
        w = self.window
        w.mode.setCurrentIndex(w.mode.findData("clean"))
        w.export_xml.setChecked(True)
        w.ai.setChecked(True)
        w.refresh_mode()
        self.assertIn("清理报告", w.mode_note.text())
        self.assertTrue(w.export_xml.isHidden())
        self.assertTrue(w.fps.isHidden())
        self.assertFalse(w.options().ai)

    def test_row_shows_what_was_cleaned(self):
        w = self.window
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.srt"
            path.write_text(render(cues("x")), encoding="utf-8")
            w.add_files([str(path)])
        w.job_event(("file", 0, "处理中", ""))
        w.job_event(("file", 0, "已完成", "删除 30 条，修正 0 条"))
        self.assertEqual(w.table.item(0, 2).text(), "删除 30 条，修正 0 条")
        self.assertIn(("结果", "删除 30 条，修正 0 条", "normal"), w.row_progress[0].detail_rows())


if __name__ == "__main__":
    unittest.main()
