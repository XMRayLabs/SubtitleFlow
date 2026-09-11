"""两个功能页共用的控件：后台任务线程与可拖拽的文件列表。"""
from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QTableWidget, QHeaderView, QAbstractItemView


class Worker(QThread):
    event = Signal(object)
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, action, secret=""):
        super().__init__()
        self.action, self.secret = action, secret

    def run(self):
        try:
            self.succeeded.emit(self.action(lambda *args: self.event.emit(args)))
        except Exception as exc:
            message = str(exc)
            self.failed.emit(message.replace(self.secret, "[REDACTED]") if self.secret else message)


class FileTable(QTableWidget):
    files_dropped = Signal(list)

    def __init__(self, first_column="SRT 文件", hint="将 SRT 字幕拖到这里", subhint="支持多个文件，也可以点击右上方添加"):
        super().__init__(0, 3)
        self.hint, self.subhint = hint, subhint
        self.setHorizontalHeaderLabels([first_column, "状态", "进度 / 说明"])
        self.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DropOnly)

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.rowCount() == 0:
            painter = QPainter(self.viewport())
            painter.setPen(QColor("#718099"))
            font = painter.font()
            font.setPointSize(13)
            painter.setFont(font)
            painter.drawText(self.viewport().rect().adjusted(0, -14, 0, -14), Qt.AlignCenter, self.hint)
            font.setPointSize(10)
            painter.setFont(font)
            painter.setPen(QColor("#a0aabc"))
            painter.drawText(self.viewport().rect().adjusted(0, 43, 0, 43), Qt.AlignCenter, self.subhint)
            painter.end()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        self.files_dropped.emit([url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()])
        event.acceptProposedAction()
