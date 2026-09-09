"""Isolated packaged smoke run: no real preferences, keychain or network."""
import json
from pathlib import Path
import tempfile
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from .gui import Window
from .srt import Cue, render


def run(destination):
    destination = Path(destination).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    window = Window(load_preferences=False)
    window.save_settings = lambda: None
    window.repo.clear()
    temporary = tempfile.TemporaryDirectory(prefix="subtitleflow-smoke-")
    root = Path(temporary.name)
    source = root / "字幕.srt"
    source.write_text(render([Cue(1, 0, 2000, "这"), Cue(2, 2000, 5000, "是测试。")]), encoding="utf-8")
    window.add_files([str(source)])
    window.mode.setCurrentIndex(window.mode.findData("merge"))
    window.output.setText(str(root / "out"))
    window.export_xml.setChecked(True)
    window.show()
    import sys
    import PySide6
    import ssl
    from .gui import native_keyring
    outcome = {"ok": False, "frozen": bool(getattr(sys, "frozen", False)),
               "executable": sys.executable, "qt_module": PySide6.__file__,
               "tls_available": bool(ssl.OPENSSL_VERSION),
               "credential_backend": type(native_keyring()).__name__}
    def start():
        window.start()
        if window.job_worker is None:
            app.quit()
            return
        def finished():
            try:
                report = json.loads((window.last_root / "report.json").read_text(encoding="utf-8"))
                outcome["ok"] = report["status"] == "done"
                outcome["status"] = window.table.item(0, 1).text()
                outcome["fcpxml_created"] = (window.last_root / "转换后的fcpxml/字幕.fcpxml").exists()
                outcome["ok"] = outcome["ok"] and outcome["fcpxml_created"]
                outcome["merged"] = (window.last_root / "合并后的srt/字幕.srt").read_text(encoding="utf-8")
                window.grab().save(str(destination.with_suffix(".png")))
            finally:
                QTimer.singleShot(0, app.quit)
        window.job_worker.finished.connect(finished)
    QTimer.singleShot(50, start)
    QTimer.singleShot(20000, app.quit)
    app.exec()
    destination.write_text(json.dumps(outcome, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.cleanup()
    return 0 if outcome["ok"] else 1
