"""Original visual design; uses system fonts and vector-drawn application artwork."""
import sys
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap, QPen
from PySide6.QtWidgets import QApplication

STYLE = """
QMainWindow, QDialog { background: #f5f7fb; }
QWidget { color: #20283b; font-size: 13px; }
QLabel { background: transparent; }
QLabel#brand { font-size: 23px; font-weight: 700; color: #172238; }
QLabel#subtitle, QLabel#muted { color: #7a8498; }
QLabel#section { font-size: 15px; font-weight: 600; }
QFrame#card, QWidget#card { background: white; border: 1px solid #e6eaf1; border-radius: 14px; }
QPushButton { background: white; border: 1px solid #dce2ec; border-radius: 8px; padding: 8px 15px; color: #39455e; font-weight: 500; }
QPushButton:hover { background: #f0f3ff; border-color: #bac7ef; }
QPushButton:pressed { background: #e8edff; }
QPushButton:disabled { color: #abb3c2; background: #f5f6f9; border-color: #edf0f5; }
QPushButton#primary { background: #5264e8; border: 1px solid #5264e8; color: white; padding: 11px 28px; font-weight: 600; }
QPushButton#primary:hover { background: #4454d2; }
QPushButton#primary:disabled { background: #a8b1ef; border-color: #a8b1ef; }
QPushButton#quiet { background: transparent; border: none; color: #728099; padding: 7px 10px; }
QPushButton#quiet:hover { background: #edf0f8; color: #4354c3; }
QLineEdit, QComboBox, QDoubleSpinBox, QSpinBox { background: #f8f9fc; border: 1px solid #e1e6ef; border-radius: 7px; padding: 7px 10px; min-height: 18px; selection-background-color: #5264e8; }
QLineEdit:focus, QComboBox:focus, QDoubleSpinBox:focus, QSpinBox:focus { border-color: #7a89ed; background: white; }
QLineEdit:disabled, QComboBox:disabled { color: #a4adbf; background: #f3f5f8; }
QComboBox QAbstractItemView { background: white; border: 1px solid #e1e6ef; selection-background-color: #edf0ff; selection-color: #3e50cb; padding: 5px; }
QCheckBox { spacing: 8px; background: transparent; }
QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid #bdc7d9; border-radius: 4px; background: white; }
QCheckBox::indicator:checked { background: #5264e8; border-color: #5264e8; }
QCheckBox::indicator:hover { border-color: #5264e8; }
QComboBox::drop-down { border: none; width: 24px; }
QSpinBox::up-button, QDoubleSpinBox::up-button { border: none; width: 18px; }
QSpinBox::down-button, QDoubleSpinBox::down-button { border: none; width: 18px; }
QTableWidget { background: white; alternate-background-color: #fafbfe; border: none; gridline-color: #f0f2f7; selection-background-color: #edf0ff; selection-color: #3445b5; }
QTableWidget::item { padding: 10px; border-bottom: 1px solid #f0f2f7; }
QHeaderView::section { background: #fafbfe; color: #8a93a5; border: none; border-bottom: 1px solid #edf0f5; padding: 10px; font-size: 12px; }
QTableCornerButton::section { border: none; background: white; }
QProgressBar { border: none; border-radius: 3px; background: #e8ecf5; min-height: 5px; max-height: 5px; }
QProgressBar::chunk { background: #6376ee; border-radius: 3px; }
QMenu { background: white; border: 1px solid #e3e7ef; padding: 6px; }
QMenu::item { padding: 9px 24px; border-radius: 5px; }
QMenu::item:selected { background: #edf0ff; }
QScrollArea { border: none; background: transparent; }
QScrollBar:vertical { background: transparent; width: 8px; }
QScrollBar::handle:vertical { background: #d6dce8; border-radius: 4px; min-height: 25px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QToolTip { background: #26334a; color: white; border: none; padding: 6px; }
"""


def logo(size=128):
    canvas = QPixmap(size, size)
    canvas.fill(Qt.transparent)
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.scale(size / 128, size / 128)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor("#5264e8"))
    painter.drawRoundedRect(QRectF(4, 4, 120, 120), 29, 29)
    painter.setBrush(QColor("#ffffff"))
    painter.drawRoundedRect(QRectF(29, 32, 70, 52), 12, 12)
    painter.setPen(QPen(QColor("#5264e8"), 6, Qt.SolidLine, Qt.RoundCap))
    painter.drawLine(43, 49, 83, 49)
    painter.drawLine(43, 66, 68, 66)
    painter.setPen(QPen(QColor("#c7d0ff"), 6, Qt.SolidLine, Qt.RoundCap))
    painter.drawLine(39, 98, 62, 98)
    painter.drawLine(74, 98, 88, 98)
    painter.end()
    return canvas


def apply():
    app = QApplication.instance()
    app.setStyle("Fusion")
    font = QFont("Microsoft YaHei UI" if sys.platform == "win32" else "PingFang SC")
    font.setPointSize(10)
    app.setFont(font)
    family = "Microsoft YaHei UI" if sys.platform == "win32" else "PingFang SC"
    app.setStyleSheet(STYLE + '\nQWidget { font-family: "' + family + '"; }')
    from pathlib import Path
    assets = (Path(__file__).resolve().parent.parent / "assets").as_posix()
    app.setStyleSheet(app.styleSheet() + (
        '\nQComboBox::down-arrow { image: url("' + assets + '/down.png"); width: 12px; height: 12px; }'
        '\nQSpinBox::up-arrow, QDoubleSpinBox::up-arrow { image: url("' + assets + '/up.png"); width: 10px; height: 10px; }'
        '\nQSpinBox::down-arrow, QDoubleSpinBox::down-arrow { image: url("' + assets + '/down.png"); width: 10px; height: 10px; }'
        '\nQCheckBox::indicator:checked { image: url("' + assets + '/check.png"); }'
    ))
    app.setWindowIcon(QIcon(logo()))
