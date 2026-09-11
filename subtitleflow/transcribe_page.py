"""音频转录页：拖入音频或视频，转录为源文件旁的 SRT。与字幕处理页各自独立运行。"""
from pathlib import Path
import threading
import time

from PySide6.QtCore import QUrl, QTimer
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import QWidget, QFrame, QVBoxLayout, QHBoxLayout, QSpinBox, QProgressBar, QFileDialog, QTableWidgetItem

from .transcriber_install import Installer, fetch_release, support_problem
from .transcription import TranscribeJob, fmt_time, is_media, MEDIA_SUFFIXES
from .updates import DEFAULT_REPO
from .widgets import FileTable, Worker

DEFAULT_SEGMENT_MINUTES = 5
STATUS_COLORS = {"已完成": "#16856b", "失败": "#d14d61", "转录中": "#5264e8", "处理中": "#5264e8"}
RETRYABLE = ("失败", "已取消", "未处理")


class TranscribePage(QWidget):
    def __init__(self, window):
        super().__init__()
        from .layout import row, label, button
        self.window = window
        self.paths: list[Path] = []
        self.worker = None
        self.mode = "transcribe"             # 当前后台任务：transcribe（转录）或 install（安装转录服务）
        self.cancel = threading.Event()
        self.fractions = {}
        self.transcription_complete = False
        self.rows: list[int] = []            # 本次任务处理的行（重试时只是其中一部分）
        self.outputs: dict[int, Path] = {}   # 行 → 实际保存的 SRT 路径
        self.last_output_row = None          # 最近完成的行，「查看结果」默认打开它
        self.install_clock = None            # (开始时间, 开始时的已下载字节)，用于计算安装速度

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
        segment_row = row(label("分段时长", "section"), self.segment_minutes, label("切点取前后 2 分钟内的静音处；显存较小或转录失败时可以调短"))
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
        self.open_button = button("查看结果", self.open_output, "quiet")
        self.open_button.hide()
        self.retry_button = button("重试", self.retry, "quiet")
        self.retry_button.hide()
        self.cancel_button = button("取消", self.cancel_job)
        self.cancel_button.hide()
        self.start_button = button("开始转录", self.primary_action, "primary")
        for item in (self.open_button, self.retry_button, self.cancel_button, self.start_button):
            footer.addWidget(item)
        page.addLayout(footer)

    # ---- 转录服务状态与安装 ---------------------------------------------

    def refresh_state(self, update_status=False):
        """根据转录服务是否可用，切换主按钮：开始转录 / 安装转录服务 / 暂不支持。"""
        if self.busy():
            return
        if self.window.transcription_service is not None:
            self.start_button.setText("开始转录")
            self.start_button.setEnabled(True)
            return
        if self.window.transcription_outdated:
            self.start_button.setText("更新转录服务")
            self.start_button.setEnabled(True)
            if update_status:
                self.status.setText("转录服务需要更新才能与当前版本的软件配合使用")
            return
        problem = support_problem()
        self.start_button.setText("暂不支持" if problem else "安装转录服务")
        self.start_button.setEnabled(not problem)
        if update_status:
            self.status.setText(problem or "首次使用需要先安装转录服务（需要下载数 GB），也可以先添加文件")

    def primary_action(self):
        if self.window.transcription_service is None:
            self.begin_install()
        else:
            self.start()

    def begin_install(self):
        if self.busy() or support_problem():
            return
        repo = self.window.repo.text().strip() or DEFAULT_REPO
        self.status.setText("正在获取转录服务信息…")
        self.start_button.setEnabled(False)
        self.window.launch(lambda _: fetch_release(repo), lambda release: self.confirm_install(release, repo),
                           self.install_failed)

    def install_failed(self, message):
        self.refresh_state()
        if self.cancel.is_set():
            self.status.setText("安装已暂停，再次点击「安装转录服务」会从断点继续")
            return
        self.status.setText(f"转录服务安装失败：{message}")
        self.window.error(message)

    def confirm_install(self, release, repo):
        from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLineEdit
        from .layout import row, label, button
        dialog = QDialog(self)
        dialog.setWindowTitle("安装转录服务")
        dialog.setMinimumWidth(560)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(14)
        layout.addWidget(label("安装转录服务", "section"))
        info = label(f"需要下载约 {release.total_size / 1024 ** 3:.1f} GB（转录服务与模型），"
                     "解压后占用的空间比下载量更大，请确保安装位置空间充足。\n"
                     "中途中断不要紧，下次安装会从断点继续。")
        info.setWordWrap(True)
        layout.addWidget(info)
        folder = QLineEdit(str(self.window.transcriber_root))

        def choose():
            chosen = QFileDialog.getExistingDirectory(dialog, "选择安装位置", folder.text())
            if chosen:
                folder.setText(str(Path(chosen) / "SubtitleFlow"))

        layout.addLayout(row(label("安装到"), folder, button("选择", choose)))
        buttons = QDialogButtonBox()
        buttons.addButton("开始安装", QDialogButtonBox.AcceptRole)
        buttons.addButton("取消", QDialogButtonBox.RejectRole)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.Accepted or not folder.text().strip():
            self.refresh_state()
            return
        self.window.transcriber_root = Path(folder.text().strip())
        self.window.save_preferences()
        self.run_install(release, repo)

    def run_install(self, release, repo):
        self.cancel = threading.Event()
        root, cancel = self.window.transcriber_root, self.cancel
        self.mode = "install"
        self.install_clock = None
        self.set_running(True)
        self.status.setText("正在安装转录服务…")
        self.worker = Worker(lambda event: Installer(release, root, cancel, lambda *p: event("install", *p), repo=repo).run())
        self.window.workers.append(self.worker)
        self.worker.event.connect(self.install_event)
        self.worker.failed.connect(self.install_failed)
        self.worker.succeeded.connect(lambda _: self.window.refresh_transcription_service())
        self.worker.succeeded.connect(lambda _: self.window.warm_up_transcription())
        self.worker.succeeded.connect(lambda _: self.status.setText("转录服务已安装，可以开始转录"))
        self.worker.finished.connect(self.job_finished)
        self.worker.start()

    def install_event(self, args):
        _, done, total, step = args
        now = time.monotonic()
        if self.install_clock is None:
            self.install_clock = (now, done)   # 续传时从已有进度开始计速
        started, first = self.install_clock
        self.progress.setValue(round(1000 * done / max(total, 1)))
        text = f"{step}：{done / 1024 ** 3:.2f} GB / {total / 1024 ** 3:.2f} GB"
        speed = (done - first) / (now - started) if now - started >= 2 else 0
        if speed > 0:
            text += f" · {speed / 1024 ** 2:.1f} MB/s · 剩余约 {fmt_time((total - done) / speed)}"
        self.status.setText(text)

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
        self.outputs.clear()
        self.update_result_buttons()
        self.table.setRowCount(0)
        self.file_count.setText("0 个文件")
        self.status.setText("拖入音频或视频，即可开始")
        self.progress.setValue(0)
        self.table.viewport().update()

    # ---- 转录 ----------------------------------------------------------

    def unfinished_rows(self):
        return [i for i in range(self.table.rowCount()) if self.table.item(i, 1).text() in RETRYABLE]

    def retry(self):
        rows = self.unfinished_rows()
        if rows:
            self.start(rows)

    def start(self, rows=None):
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
        self.mode = "transcribe"
        self.transcription_complete = False
        self.rows = list(range(len(self.paths))) if rows is None else rows
        self.cancel = threading.Event()
        self.fractions = {}
        self.progress.setValue(0)
        for row_index in self.rows:
            self.outputs.pop(row_index, None)
            self.set_row(row_index, "等待", "")
        self.set_running(True)
        paths = [self.paths[i] for i in self.rows]
        minutes, cancel = self.segment_minutes.value(), self.cancel
        self.worker = Worker(lambda event: TranscribeJob(paths, minutes * 60, service, cancel, event).run())
        self.window.workers.append(self.worker)
        self.worker.event.connect(self.job_event)
        self.worker.failed.connect(self.transcription_failed)
        self.worker.finished.connect(self.job_finished)
        self.worker.start()

    def set_row(self, index, status, detail):
        self.table.item(index, 1).setText(status)
        self.table.item(index, 1).setForeground(QColor(STATUS_COLORS.get(status, "#7a8498")))
        self.table.item(index, 2).setText(detail)

    def job_event(self, args):
        kind, *values = args
        if kind == "file":
            row_index, status, detail = self.rows[values[0]], values[1], values[2]
            self.set_row(row_index, status, detail)
            if status == "已完成":
                self.outputs[row_index] = Path(detail)
                self.last_output_row = row_index
            if status in RETRYABLE:
                self.fractions[row_index] = 1.0
        elif kind == "progress":
            self.fractions[self.rows[values[0]]] = values[1]
        elif kind == "warning":
            row_index = self.rows[values[0]]
            for col in range(3):
                self.table.item(row_index, col).setToolTip(values[1])
            self.table.item(row_index, 1).setText("已完成（注意）")
            self.table.item(row_index, 1).setForeground(QColor("#b7791f"))
        elif kind == "incompatible":
            self.window.mark_transcription_outdated()
        elif kind == "complete":
            self.transcription_complete = True
            self.status.setText({"done": "全部转录完成，SRT 保存位置见「进度 / 说明」",
                                 "partial": "部分文件转录失败，可以重试未完成的文件",
                                 "cancelled": "转录已取消，已完成的 SRT 已保留"}[values[0]])
            QTimer.singleShot(0, self._complete_job)
        if self.rows:
            self.progress.setValue(round(1000 * sum(self.fractions.values()) / len(self.rows)))

    def transcription_failed(self, message):
        self.window.error(message)
        self.transcription_complete = True
        QTimer.singleShot(0, self._complete_job)

    def update_result_buttons(self):
        idle = not self.busy()
        self.open_button.setVisible(idle and bool(self.outputs))
        can_retry = idle and bool(self.unfinished_rows())
        self.retry_button.setVisible(can_retry)
        self.retry_button.setEnabled(can_retry)

    def open_output(self):
        selected = [i.row() for i in self.table.selectionModel().selectedRows() if i.row() in self.outputs]
        # 没有选中已完成的行时，打开最近完成的那个文件所在目录
        row_index = selected[0] if selected else self.last_output_row
        target = self.outputs.get(row_index)
        if target and target.parent.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(target.parent)))

    def job_finished(self):
        # finished 信号发出时线程可能还没完全退出；先等它结束，再释放 QThread 对象
        if self.mode == "transcribe" and not self.transcription_complete:
            return
        QTimer.singleShot(0, self._complete_job)

    def _complete_job(self):
        if self.worker is None:
            return
        self.worker.wait()
        self.window.workers.remove(self.worker)
        self.worker = None
        self.set_running(False)
        self.window.worker_finished()

    def cancel_job(self):
        if self.busy():
            self.cancel.set()
            self.cancel_button.setEnabled(False)
            self.status.setText("正在取消…")

    def set_running(self, active):
        for widget in (self.settings_group, self.add_button, self.clear_button, self.start_button):
            widget.setEnabled(not active)
        self.cancel_button.setVisible(active)
        self.cancel_button.setEnabled(active)
        self.window.uninstall_action.setEnabled(not active)
        if active:
            self.start_button.setText("安装中…" if self.mode == "install" else "转录中…")
            if self.mode == "transcribe":
                self.status.setText("正在转录…")
            self.open_button.hide()
            self.retry_button.hide()
        else:
            self.update_result_buttons()
            self.refresh_state()
