"""音频转录页：拖入音频或视频，转录为源文件旁的 SRT。与字幕处理页各自独立运行。"""
from pathlib import Path
import threading

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QWidget, QFrame, QVBoxLayout, QHBoxLayout, QSpinBox, QProgressBar, QFileDialog, QTableWidgetItem

from .transcription import TranscribeJob, is_media, MEDIA_SUFFIXES
from .widgets import FileTable, Worker

DEFAULT_SEGMENT_MINUTES = 5
STATUS_COLORS = {"已完成": "#16856b", "失败": "#d14d61", "转录中": "#5264e8", "处理中": "#5264e8"}


class TranscribePage(QWidget):
    def __init__(self, window):
        super().__init__()
        from .layout import row, label, button
        self.window = window
        self.paths: list[Path] = []
        self.worker = None
        self.cancel = threading.Event()
        self.fractions = {}

        page = QVBoxLayout(self)
        page.setContentsMargins(0, 0, 0, 0)
        page.setSpacing(17)

        files = QFrame()
        files.setObjectName("card")
        file_layout = QVBoxLayout(files)
        file_layout.setContentsMargins(18, 15, 18, 8)
        file_layout.setSpacing(10)
        bar = QHBoxLayout()
        bar.addWidget(label("音视频文件", "section"))
        self.file_count = label("0 个文件")
        bar.addWidget(self.file_count)
        bar.addStretch()
        self.clear_button = button("清空", self.clear_files, "quiet")
        self.add_button = button("＋ 添加文件", self.choose_files)
        bar.addWidget(self.clear_button)
        bar.addWidget(self.add_button)
        file_layout.addLayout(bar)
        self.table = FileTable("源文件", "将音频或视频拖到这里", "支持多个文件，SRT 会保存在源文件旁边")
        self.table.files_dropped.connect(self.add_files)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(46)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setMinimumHeight(170)
        file_layout.addWidget(self.table, 1)
        page.addWidget(files, 1)

        self.settings_group = QFrame()
        self.settings_group.setObjectName("card")
        settings = QVBoxLayout(self.settings_group)
        settings.setContentsMargins(20, 17, 20, 16)
        settings.setSpacing(14)
        self.segment_minutes = QSpinBox()
        self.segment_minutes.setRange(1, 10)
        self.segment_minutes.setValue(DEFAULT_SEGMENT_MINUTES)
        self.segment_minutes.setSuffix(" 分钟")
        self.segment_minutes.setFixedWidth(115)
        segment_row = row(label("分段时长", "section"), self.segment_minutes, label("显存较小或转录失败时可以调短"))
        segment_row.addStretch()
        settings.addLayout(segment_row)
        page.addWidget(self.settings_group)

        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 1000)
        page.addWidget(self.progress)

        footer = QHBoxLayout()
        self.status = label("拖入音频或视频，即可开始")
        self.status.setWordWrap(True)
        footer.addWidget(self.status, 1)
        self.start_button = button("开始转录", self.start, "primary")
        footer.addWidget(self.start_button)
        page.addLayout(footer)

    # ---- 设置 ----------------------------------------------------------

    def settings(self):
        return {"segment_minutes": self.segment_minutes.value()}

    def apply_settings(self, data):
        self.segment_minutes.setValue(int(data.get("segment_minutes", DEFAULT_SEGMENT_MINUTES)))

    # ---- 文件列表 -------------------------------------------------------

    def busy(self):
        return self.worker is not None and self.worker.isRunning()

    def choose_files(self):
        patterns = " ".join("*" + suffix for suffix in sorted(MEDIA_SUFFIXES))
        paths, _ = QFileDialog.getOpenFileNames(self, "选择音频或视频", "", f"音视频文件 ({patterns})")
        self.add_files(paths)

    def add_files(self, paths):
        if self.busy():
            return
        for value in paths:
            path = Path(value).resolve()
            if path.is_file() and is_media(path) and path not in self.paths:
                self.paths.append(path)
                row_index = self.table.rowCount()
                self.table.insertRow(row_index)
                for col, text in enumerate((path.name, "等待", "")):
                    self.table.setItem(row_index, col, QTableWidgetItem(text))
        self.file_count.setText(f"{len(self.paths)} 个文件")
        self.status.setText(f"已添加 {len(self.paths)} 个音视频文件")
        self.table.viewport().update()

    def clear_files(self):
        if self.busy():
            return
        self.paths.clear()
        self.table.setRowCount(0)
        self.file_count.setText("0 个文件")
        self.status.setText("拖入音频或视频，即可开始")
        self.progress.setValue(0)
        self.table.viewport().update()

    # ---- 转录 ----------------------------------------------------------

    def start(self):
        if self.busy():
            return
        if not self.paths:
            self.window.error("请先添加音频或视频文件")
            return
        service = self.window.transcription_service
        if service is None:
            self.window.error("尚未安装转录服务")
            return
        self.window.save_preferences()
        self.cancel = threading.Event()
        self.fractions = {}
        self.progress.setValue(0)
        for index in range(len(self.paths)):
            self.set_row(index, "等待", "")
        self.set_running(True)
        paths, minutes, cancel = list(self.paths), self.segment_minutes.value(), self.cancel
        self.worker = Worker(lambda event: TranscribeJob(paths, minutes * 60, service, cancel, event).run())
        self.window.workers.append(self.worker)
        self.worker.event.connect(self.job_event)
        self.worker.failed.connect(self.window.error)
        self.worker.finished.connect(self.job_finished)
        self.worker.start()

    def set_row(self, index, status, detail):
        self.table.item(index, 1).setText(status)
        self.table.item(index, 1).setForeground(QColor(STATUS_COLORS.get(status, "#7a8498")))
        self.table.item(index, 2).setText(detail)

    def job_event(self, args):
        kind, *values = args
        if kind == "file":
            self.set_row(*values)
            if values[1] in ("失败", "已取消", "未处理"):
                self.fractions[values[0]] = 1.0
        elif kind == "progress":
            self.fractions[values[0]] = values[1]
        elif kind == "complete":
            self.status.setText({"done": "全部转录完成，SRT 已保存在源文件旁边",
                                 "partial": "部分文件转录失败，其余已完成",
                                 "cancelled": "转录已取消，已完成的 SRT 已保留"}[values[0]])
        if self.paths:
            self.progress.setValue(round(1000 * sum(self.fractions.values()) / len(self.paths)))

    def job_finished(self):
        # finished 信号发出时线程可能还没完全退出；先等它结束，再释放 QThread 对象
        self.worker.wait()
        self.window.workers.remove(self.worker)
        self.worker = None
        self.set_running(False)
        self.window.worker_finished()

    def set_running(self, active):
        for widget in (self.settings_group, self.add_button, self.clear_button, self.start_button):
            widget.setEnabled(not active)
        self.start_button.setText("转录中…" if active else "开始转录")
        if active:
            self.status.setText("正在转录…")
