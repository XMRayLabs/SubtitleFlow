import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

from subtitleflow import gui, transcription
from subtitleflow.transcription import ServiceManager

app = QApplication.instance() or QApplication([])


class NoKeyring:
    def get_password(self, *args):
        return None

    def set_password(self, *args):
        pass

    def delete_password(self, *args):
        pass


class WindowTestCase(unittest.TestCase):
    """Isolated settings, no keyring, and no background update check (which would hit the network)."""

    def setUp(self):
        self.data = tempfile.TemporaryDirectory()
        self.addCleanup(self.data.cleanup)
        for target, name, value in ((gui, "app_data", lambda: Path(self.data.name)), (gui, "native_keyring", NoKeyring),
                                    (gui.Window, "check_update", lambda self, silent=False: None),
                                    (transcription, "development_command", lambda: None)):
            patcher = patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)


class PageNavigationTests(WindowTestCase):
    def window(self):
        window = gui.Window()
        self.addCleanup(window.deleteLater)
        return window

    def test_defaults_to_subtitle_processing(self):
        window = self.window()
        self.assertIs(window.pages.currentWidget(), window.subtitle_page)
        self.assertTrue(window.nav_buttons["subtitle"].isChecked())
        self.assertIn("字幕处理", window.windowTitle())

    def test_switching_to_transcription_hides_subtitle_only_actions(self):
        window = self.window()
        window.nav_buttons["transcribe"].click()
        self.assertIs(window.pages.currentWidget(), window.transcribe_page)
        self.assertIn("音频转录", window.windowTitle())
        self.assertTrue(window.translation_button.isHidden())
        self.assertFalse(window.restore_button.isVisible())
        window.nav_buttons["subtitle"].click()
        self.assertFalse(window.translation_button.isHidden())
        self.assertTrue(window.restore_button.isVisible())

    def test_last_page_is_restored(self):
        window = self.window()
        window.nav_buttons["transcribe"].click()
        window.save_settings()
        reopened = self.window()
        self.assertIs(reopened.pages.currentWidget(), reopened.transcribe_page)

    def test_unknown_saved_page_falls_back_to_subtitle_processing(self):
        (Path(self.data.name) / "settings.json").write_text(json.dumps({"page": "nope"}), encoding="utf-8")
        window = self.window()
        self.assertIs(window.pages.currentWidget(), window.subtitle_page)


class TranscribePageTests(WindowTestCase):
    def setUp(self):
        super().setUp()
        self.window = gui.Window()
        self.addCleanup(self.window.deleteLater)
        root = Path(__file__).resolve().parent.parent
        self.window.transcription_service = ServiceManager([sys.executable, "-m", "transcriber", "--engine", "fake"], cwd=root)
        self.addCleanup(self.window.transcription_service.stop)
        self.page = self.window.transcribe_page

    def wait_idle(self):
        deadline = time.time() + 20
        while self.page.busy() or self.page.worker is not None:
            if time.time() > deadline:
                self.fail("transcription did not finish")
            app.processEvents()
            time.sleep(0.01)

    def test_only_media_files_are_added(self):
        media = Path(self.data.name) / "a.mp3"
        other = Path(self.data.name) / "a.txt"
        for path in (media, other):
            path.write_text("{}", encoding="utf-8")
        self.page.add_files([str(media), str(other), str(media)])
        self.assertEqual(self.page.paths, [media.resolve()])

    def test_start_transcribes_files_to_srt_beside_source(self):
        source = Path(self.data.name) / "talk.wav"
        source.write_text(json.dumps({"duration": 60}), encoding="utf-8")
        self.page.add_files([str(source)])
        self.page.start_button.click()
        self.wait_idle()
        self.assertEqual(self.page.table.item(0, 1).text(), "已完成")
        self.assertTrue((Path(self.data.name) / "talk.srt").exists())
        self.assertEqual(self.page.progress.value(), 1000)

    def test_retry_only_reruns_unfinished_files(self):
        bad = Path(self.data.name) / "bad.wav"
        bad.write_text(json.dumps({"duration": 60, "fail": "显存不足"}), encoding="utf-8")
        good = Path(self.data.name) / "good.wav"
        good.write_text(json.dumps({"duration": 60}), encoding="utf-8")
        self.page.add_files([str(bad), str(good)])
        self.page.start_button.click()
        self.wait_idle()
        self.assertTrue(self.page.retry_button.isEnabled())
        self.page.retry_button.click()
        self.wait_idle()
        self.assertEqual(self.page.table.item(0, 1).text(), "失败")
        self.assertEqual(self.page.table.item(1, 1).text(), "已完成")
        self.assertFalse((Path(self.data.name) / "good (1).srt").exists())


if __name__ == "__main__":
    unittest.main()
