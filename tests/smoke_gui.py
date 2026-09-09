"""Offscreen UI smoke test; does not read/write real settings or credentials."""
import os
os.environ["QT_QPA_PLATFORM"] = "windows" if os.name == "nt" else "offscreen"
from unittest.mock import patch
from PySide6.QtWidgets import QApplication
from subtitleflow.gui import Window
app = QApplication([])
with patch.object(Window, "load_settings"), patch.object(Window, "save_settings"):
    window = Window(load_preferences=False)
    window.repo.clear()
    window.show()
    app.processEvents()
    assert window.mode.currentData() == "both"
    assert window.target.value() == 10
    assert window.batch.value() == 20
    window.grab().save("build/gui-preview.png")
    window.close()
print("GUI smoke passed")
