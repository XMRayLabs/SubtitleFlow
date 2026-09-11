import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

from subtitleflow import gui

app = QApplication.instance() or QApplication([])


class NoKeyring:
    def get_password(self, *args):
        return None

    def set_password(self, *args):
        pass

    def delete_password(self, *args):
        pass


class PageNavigationTests(unittest.TestCase):
    def setUp(self):
        self.data = tempfile.TemporaryDirectory()
        self.addCleanup(self.data.cleanup)
        for name, value in (("app_data", lambda: Path(self.data.name)), ("native_keyring", NoKeyring)):
            patcher = patch.object(gui, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

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


if __name__ == "__main__":
    unittest.main()
