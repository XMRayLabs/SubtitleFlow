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

from subtitleflow import gui, transcribe_page, transcriber_install, transcription
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
                                    (transcription, "development_command", lambda: None),
                                    (transcriber_install, "default_root", lambda: Path(self.data.name) / "install")):
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
        # 生产代码每次改动 transcription_service 都会刷新转录页（见 Window.refresh_transcription_service）。
        # 这里直接注入服务，同样要刷新：否则在不支持转录的机器上（CI 没有 NVIDIA 显卡，macOS 更是不支持）
        # 主按钮仍停在「暂不支持」且处于禁用状态，click() 什么也不会发生，整行会一直停在「等待」。
        self.page.refresh_state()
        # 先把服务进程拉起来，让 wait_idle() 的等待窗口只覆盖转录本身：服务首次启动在 CI 的 macOS
        # 机器上约需 35 秒，算进去会直接顶穿 wait_idle 的 20 秒上限。
        self.window.transcription_service.ensure()

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


class ServiceStateTests(WindowTestCase):
    def window_with(self, problem):
        with patch.object(transcribe_page, "support_problem", return_value=problem):
            window = gui.Window()
        self.addCleanup(window.deleteLater)
        return window

    def test_without_service_offers_installation(self):
        page = self.window_with(None).transcribe_page
        self.assertEqual(page.start_button.text(), "安装转录服务")
        self.assertTrue(page.start_button.isEnabled())

    def test_unsupported_device_explains_why_and_offers_no_install(self):
        page = self.window_with("当前设备暂不支持音频转录（需要 NVIDIA 显卡）").transcribe_page
        self.assertEqual(page.start_button.text(), "暂不支持")
        self.assertFalse(page.start_button.isEnabled())
        self.assertIn("NVIDIA", page.status.text())

    def install_fake_service(self, api_version=1):
        base = Path(self.data.name) / "install" / "transcriber"
        exe = base / "service" / "1.0.0" / "S" / "S.exe"
        exe.parent.mkdir(parents=True)
        exe.write_bytes(b"MZ")
        (base / "models" / "rev").mkdir(parents=True)
        (base / "installed.json").write_text(json.dumps(
            {"version": "1.0.0", "api_version": api_version, "exe": "service/1.0.0/S/S.exe", "model_dir": "models/rev"}),
            encoding="utf-8")
        return exe

    def test_installed_service_is_used(self):
        exe = self.install_fake_service()
        window = self.window_with(None)
        self.assertEqual(window.transcription_installed.exe, exe)
        self.assertEqual(window.transcription_service.command[0], str(exe))
        self.assertEqual(window.transcribe_page.start_button.text(), "开始转录")

    def test_incompatible_installed_service_asks_for_update(self):
        self.install_fake_service(api_version=0)
        window = self.window_with(None)
        self.assertIsNone(window.transcription_service)
        self.assertEqual(window.transcribe_page.start_button.text(), "更新转录服务")
        self.assertIn("需要更新", window.transcribe_page.status.text())

    def test_uninstall_is_offered_only_when_installed_and_returns_to_install_state(self):
        self.assertFalse(self.window_with(None).uninstall_action.isVisible())
        self.install_fake_service()
        window = self.window_with(None)
        self.assertTrue(window.uninstall_action.isVisible())
        with patch.object(gui.QMessageBox, "question", return_value=gui.QMessageBox.Yes), \
                patch.object(gui.QMessageBox, "information"), patch.object(transcribe_page, "support_problem", return_value=None):
            window.uninstall_action.trigger()
        self.assertIsNone(transcriber_install.installed_service(Path(self.data.name) / "install"))
        self.assertFalse(window.uninstall_action.isVisible())
        self.assertEqual(window.transcribe_page.start_button.text(), "安装转录服务")


if __name__ == "__main__":
    unittest.main()
