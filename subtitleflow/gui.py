from dataclasses import asdict, replace
import json
from pathlib import Path
import sys
import threading

from PySide6.QtCore import QStandardPaths, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QVBoxLayout, QFileDialog, QTableWidgetItem, QMessageBox,
)
from . import __version__
from .api import APIConfig, Client
from .jobs import Job, JobOptions
from .merge import MergeOptions
from . import updates
from .safety import safe_tree, read_json
from .widgets import FileTable, Worker


def app_data():
    path = Path(QStandardPaths.writableLocation(QStandardPaths.AppDataLocation))
    path.mkdir(parents=True, exist_ok=True)
    return path


def native_keyring():
    if sys.platform == "win32":
        from keyring.backends.Windows import WinVaultKeyring
        return WinVaultKeyring()
    if sys.platform == "darwin":
        from keyring.backends.macOS import Keyring
        return Keyring()
    raise RuntimeError("记住密钥仅支持 Windows 和 macOS 系统凭据存储")


class Window(QMainWindow):
    def __init__(self, load_preferences=True):
        super().__init__()
        self.paths, self.last_root = [], None
        self.workers = []
        self.job_worker = None
        self.cancel = threading.Event()
        self.update_cancel = threading.Event()
        self.pending_update = None
        self.update_busy = False
        from .transcriber_install import default_root
        self.transcriber_root = default_root()
        self.transcription_service = None
        self.transcription_installed = None
        self.transcription_outdated = False
        from .layout import build
        build(self, FileTable)
        if load_preferences:
            self.load_settings()
        self.refresh_transcription_service()
        QTimer.singleShot(1500, lambda: self.check_update(True))
        if load_preferences:
            self.schedule_models()
            self.warm_up_transcription()

    def refresh_transcription_service(self):
        """优先使用已安装的转录服务，其次是开发环境；都没有时转录页显示安装入口。
        已安装的服务接口版本不兼容时，转录页提示更新转录服务。"""
        from .transcriber_install import installed_service
        from .transcription import ServiceManager, TRANSCRIBER_API_VERSION, development_command
        if self.transcription_service:
            self.transcription_service.stop()
        self.transcription_service = None
        self.transcription_installed = installed_service(self.transcriber_root)
        installed = self.transcription_installed
        self.transcription_outdated = installed is not None and installed.api_version != TRANSCRIBER_API_VERSION
        if installed and not self.transcription_outdated:
            self.transcription_service = ServiceManager(installed.command(), cwd=installed.exe.parent)
        elif not installed:
            command = development_command()
            self.transcription_service = ServiceManager(*command) if command else None
        self.uninstall_action.setVisible(installed is not None)
        self.transcribe_page.refresh_state(update_status=True)

    def uninstall_transcriber(self):
        from .transcriber_install import uninstall
        if self.transcribe_page.busy():
            self.error("转录或安装进行中，请结束后再卸载转录服务")
            return
        if QMessageBox.question(self, "卸载转录服务", "将删除转录服务和模型文件，之后需要重新下载才能转录。确定卸载？") != QMessageBox.Yes:
            return
        if self.transcription_service:
            self.transcription_service.stop()
        try:
            freed = uninstall(self.transcriber_root)
        except OSError as exc:
            self.error(f"卸载未完成：{exc}")
            return
        finally:
            self.refresh_transcription_service()
        QMessageBox.information(self, "卸载转录服务", f"已卸载转录服务，释放 {freed / 1024 ** 3:.1f} GB 空间")

    def warm_up_transcription(self):
        """随主程序在后台启动转录服务；服务启动时不加载模型，几乎不占资源。失败时等到转录时再重试。"""
        service = self.transcription_service
        if service is None:
            return
        from .transcription import ServiceError

        def start():
            try:
                service.ensure()
            except ServiceError:
                pass

        threading.Thread(target=start, daemon=True).start()

    def show_page(self, key):
        from .layout import PAGES
        names = dict(PAGES)
        if key not in names:
            key = PAGES[0][0]
        self.page = key
        self.pages.setCurrentWidget(self.subtitle_page if key == "subtitle" else self.transcribe_page)
        self.nav_buttons[key].setChecked(True)
        # Translation settings and task restore only concern subtitle processing.
        subtitle = key == "subtitle"
        self.translation_button.setVisible(subtitle)
        self.restore_button.setVisible(subtitle)
        self.setWindowTitle(f"SubtitleFlow · {names[key]} {__version__}")

    def show_translation(self):
        if not self.busy():
            self.api_dialog.open()

    def save_translation(self):
        try:
            self.save_settings()
        except Exception as exc:
            self.error(str(exc))
            return
        self.api_dialog.accept()
        self.status.setText("翻译设置已保存")

    def show_update(self):
        if not self.busy():
            self.update_dialog.open()

    def any_busy(self):
        return self.busy() or self.transcribe_page.busy()

    def save_preferences(self):
        try:
            self.save_settings()
        except Exception as exc:
            self.status.setText(f"设置未能保存：{exc}")

    def worker_finished(self):
        """任一页的任务结束后调用；所有任务都结束才安装已下载的更新。"""
        if self.pending_update and not self.any_busy():
            self.install_update()

    def refresh_mode(self):
        mode = self.mode.currentData()
        merging = mode in ("merge", "both")
        self.merge_row.setVisible(merging)
        self.advanced_button.setVisible(merging)
        if not merging:
            self.advanced.hide()
        self.export_xml.setVisible(mode != "fcpxml")
        xml = mode == "fcpxml" or self.export_xml.isChecked()
        self.fps.setVisible(xml)
        self.fps_label.setVisible(xml)
        self.mode_note.setText({
            "both": "先合并完整句子，再翻译为简体中文。",
            "merge": "完整句子优先，保留原文与时间轴。无需翻译服务。",
            "translate": "保留每条字幕的时间轴，翻译为简体中文。",
            "fcpxml": "转换为 Final Cut Pro 可编辑标题，无需翻译服务。"
        }[mode])

    def busy(self):
        return self.job_worker is not None and self.job_worker.isRunning()

    def launch(self, action, success, failure=None, secret=""):
        worker = Worker(action, secret)
        self.workers.append(worker)
        worker.succeeded.connect(success)
        worker.failed.connect(failure or self.error)
        worker.finished.connect(lambda: self.workers.remove(worker))
        worker.start()
        return worker

    def error(self, message):
        QMessageBox.warning(self, "处理提示", message)

    def choose_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "选择字幕文件", "", "SRT 字幕 (*.srt)")
        self.add_files(paths)

    def add_files(self, paths):
        if self.busy():
            return
        for value in paths:
            path = Path(value).resolve()
            if path.is_file() and path.suffix.lower() == ".srt" and path not in self.paths:
                self.last_root = None
                self.retry_button.setEnabled(False)
                self.paths.append(path)
                row = self.table.rowCount()
                self.table.insertRow(row)
                for col, text in enumerate((path.name, "等待", "")):
                    self.table.setItem(row, col, QTableWidgetItem(text))
        self.file_count.setText(f"{len(self.paths)} 个文件")
        self.status.setText(f"已添加 {len(self.paths)} 个 SRT 文件")
        self.table.viewport().update()

    def clear_files(self):
        self.paths.clear()
        self.table.setRowCount(0)
        self.file_count.setText("0 个文件")
        self.status.setText("拖入字幕，即可开始")
        self.table.viewport().update()
        self.last_root = None
        self.retry_button.setEnabled(False)

    def choose_output(self):
        directory = QFileDialog.getExistingDirectory(self, "选择输出目录", self.output.text())
        if directory:
            self.output.setText(directory)

    def api_config(self):
        return APIConfig(self.base.text().strip(), self.key.text(), self.model.currentText().strip(), allow_local_self_signed=self.local_cert.isChecked())

    def options(self):
        return JobOptions(self.mode.currentData(),
                          MergeOptions(self.target.value(), self.silence.value(), self.tolerance.value()),
                          self.batch.value(), self.ai.isChecked() and self.mode.currentData() not in ("translate", "fcpxml"),
                          self.export_xml.isChecked(), self.fps.currentText())

    def save_settings(self):
        data = {"base": self.base.text(), "model": self.model.currentText(), "output": self.output.text(),
                "repo": self.repo.text().strip(), "remember": self.remember.isChecked(),
                "local_cert": self.local_cert.isChecked(), "page": self.page,
                "transcribe": self.transcribe_page.settings(), "transcriber_root": str(self.transcriber_root),
                "options": asdict(self.options())}
        if self.remember.isChecked():
            native_keyring().set_password("SubtitleFlow", "api-key", self.key.text())
        else:
            try:
                backend = native_keyring()
                if backend.get_password("SubtitleFlow", "api-key") is not None:
                    backend.delete_password("SubtitleFlow", "api-key")
            except Exception:
                # Do not silently claim a previously saved credential was erased.
                settings = app_data() / "settings.json"
                if settings.exists() and json.loads(settings.read_text(encoding="utf-8")).get("remember"):
                    raise RuntimeError("无法删除先前保存的密钥，请检查系统凭据存储")
        from .translate import atomic_json
        atomic_json(app_data() / "settings.json", data)

    def load_settings(self):
        try:
            data = json.loads((app_data() / "settings.json").read_text(encoding="utf-8"))
            for name in ("base", "output"):
                getattr(self, name).setText(data.get(name, getattr(self, name).text()))
            self.repo.setText(data.get("repo") or updates.DEFAULT_REPO)
            self.model.setCurrentText(data.get("model", ""))
            self.local_cert.setChecked(data.get("local_cert", False))
            self.remember.setChecked(data.get("remember", False))
            self.apply_options(data.get("options", {}))
            self.transcribe_page.apply_settings(data.get("transcribe", {}))
            if data.get("transcriber_root"):
                self.transcriber_root = Path(data["transcriber_root"])
            self.show_page(data.get("page"))
            if self.remember.isChecked():
                self.key.setText(native_keyring().get_password("SubtitleFlow", "api-key") or "")
        except FileNotFoundError:
            pass
        except Exception:
            self.status.setText("部分设置或已保存密钥无法读取，请重新填写")

    def apply_options(self, data):
        self.mode.setCurrentIndex(max(0, self.mode.findData(data.get("mode", "both"))))
        for name, value in data.get("merge", {}).items():
            if name in ("target", "silence", "tolerance"):
                getattr(self, name).setValue(value)
        self.batch.setValue(data.get("batch_size", 20))
        self.ai.setChecked(data.get("ai", False))
        self.export_xml.setChecked(data.get("export_fcpxml", False))
        self.fps.setCurrentText(data.get("fps", "25"))

    def start(self, resume=None):
        if self.busy():
            return
        if not resume and not self.paths:
            self.error("请先添加 SRT 文件")
            return
        try:
            options, api = self.options(), self.api_config()
            options.merge.validate()
            if options.mode in ("translate", "both") or options.ai:
                api.endpoint()
            if not self.output.text().strip():
                raise ValueError("请选择输出目录")
            self.save_settings()
        except Exception as exc:
            self.error(str(exc))
            return
        self.cancel = threading.Event()
        self.progress.setRange(0, max(1, len(self.paths)))
        self.progress.setValue(0)
        self.set_running(True)
        worker = Worker(lambda event: Job(self.paths, Path(self.output.text()), options, api,
                                         self.cancel, event, resume).run(), api.key)
        self.job_worker = worker
        self.workers.append(worker)
        worker.event.connect(self.job_event)
        worker.succeeded.connect(lambda root: setattr(self, "last_root", root))
        worker.failed.connect(self.error)
        worker.finished.connect(self.job_finished)
        worker.start()

    def set_running(self, active):
        for widget in (self.settings_group, self.add_button, self.clear_button, self.restore_button, self.start_button, self.translation_button):
            widget.setEnabled(not active)
        self.cancel_button.setEnabled(active)
        self.cancel_button.setVisible(active)
        self.start_button.setText("处理中…" if active else "开始处理")
        self.open_button.setVisible(self.last_root is not None)
        self.retry_button.setVisible(not active and self.last_root is not None)
        self.retry_button.setEnabled(not active and self.last_root is not None)

    def job_event(self, args):
        kind, *values = args
        if kind == "root":
            self.last_root = Path(values[0])
        elif kind == "file":
            row, status, detail = values
            self.table.item(row, 1).setText(status)
            from PySide6.QtGui import QColor
            self.table.item(row, 1).setForeground(QColor({"已完成": "#16856b", "失败": "#d14d61", "翻译中": "#5264e8", "处理中": "#5264e8"}.get(status, "#7a8498")))
            self.table.item(row, 2).setText(detail)
            finished = sum(self.table.item(i, 1).text() in ("已完成", "失败") for i in range(self.table.rowCount()))
            self.progress.setValue(finished)
        elif kind == "complete":
            self.status.setText({"done": "全部处理完成", "partial": "部分文件失败，可以重试未完成文件",
                                 "cancelled": "任务已取消，已完成结果已保留"}[values[0]])

    def job_finished(self):
        self.workers.remove(self.job_worker)
        self.job_worker = None
        self.set_running(False)
        self.worker_finished()

    def cancel_job(self):
        self.cancel.set()
        self.status.setText("正在取消；进行中的网络请求最多等待 60 秒，已完成进度会保存")

    def restore(self):
        directory = QFileDialog.getExistingDirectory(self, "选择含 report.json 的任务目录")
        if not directory:
            return
        try:
            root = safe_tree(Path(directory))
            report = read_json(root / "report.json")
            if len(report["files"]) > 10000:
                raise ValueError("任务超过 10000 个文件上限")
            self.clear_files()
            self.apply_options(report["settings"]["options"])
            # A report must never redirect the current API credential to another server.
            if report["settings"]["base_url"] != self.base.text().strip() or report["settings"]["model"] != self.model.currentText().strip():
                raise ValueError("任务 API 地址或模型与当前设置不同，请在翻译设置中自行核对后重试")
            for item in report["files"]:
                name = item["name"]
                if Path(name).name != name or ":" in name or "\\" in name:
                    raise ValueError("任务文件名不安全")
                self.paths.append(root / "原始的srt" / name)
                row = self.table.rowCount()
                self.table.insertRow(row)
                for col, text in enumerate((item["source"], "已完成" if item["status"] == "done" else "待重试", item.get("error", ""))):
                    self.table.setItem(row, col, QTableWidgetItem(text))
            self.last_root = root
            self.file_count.setText(f"{len(self.paths)} 个文件")
            self.retry_button.show()
            self.open_button.show()
            self.retry_button.setEnabled(True)
            self.status.setText("任务已载入，点击“重试未完成文件”继续；原始副本用于恢复")
        except Exception as exc:
            self.error(f"无法恢复任务：{exc}")

    def schedule_models(self):
        if self.base.text().strip() and not self.busy():
            self.models_timer.start()

    def load_models(self, checked=False, automatic=False):
        if self.busy():
            return
        config = self.api_config()
        identity = (config.base_url, config.key, config.allow_local_self_signed)
        if self.models_loading:
            self.models_timer.start()
            return
        if automatic and identity == self.last_models_identity:
            return
        try:
            config.models_endpoint()
        except ValueError as exc:
            if not automatic:
                self.error(str(exc))
            return
        self.models_loading = True
        self.models_button.setEnabled(False)
        self.status.setText("正在加载模型列表…")
        self.api_status.setText("正在加载模型列表…")
        def current_identity():
            value = self.api_config()
            return (value.base_url, value.key, value.allow_local_self_signed)
        def reset():
            self.models_loading = False
            self.models_button.setEnabled(True)
        def success(result):
            models, resolved = result
            reset()
            if identity != current_identity():
                self.schedule_models()
                return
            self.base.setText(resolved)
            selected = self.model.currentText()
            self.model.clear()
            self.model.addItems(models)
            if selected:
                self.model.setCurrentText(selected)
            self.last_models_identity = current_identity()
            self.status.setText(f"已加载 {len(models)} 个模型，请选择需要的模型")
            self.api_status.setText(f"已加载 {len(models)} 个模型")
        def failure(message):
            reset()
            if identity != current_identity():
                self.schedule_models()
                return
            self.status.setText(message)
            self.api_status.setText(message)
            if not automatic:
                self.error(message)
        def fetch(event):
            client = Client(config, threading.Event())
            resolved = client.discover_base()
            if resolved != config.base():
                event("resolved", resolved)
            client = Client(replace(config, base_url=resolved), threading.Event())
            return client.models(), resolved
        worker = self.launch(fetch, success, failure, config.key)
        def detected(event):
            nonlocal identity
            if event[0] == "resolved" and identity == current_identity():
                self.base.setText(event[1])
                identity = current_identity()
                self.status.setText("已识别本地 Open WebUI，使用 /api 路径加载模型")
        worker.event.connect(detected)

    def test_api(self):
        try:
            config = self.api_config()
            config.endpoint()
        except Exception as exc:
            self.error(str(exc))
            return
        self.test_button.setEnabled(False)
        def finished(message):
            self.test_button.setEnabled(True)
            self.status.setText(message)
            self.api_status.setText(message)
        self.launch(lambda _: Client(config, threading.Event()).test(),
                    lambda _: finished("API 连接成功"),
                    lambda message: (finished("API 连接失败"), self.error(message)), config.key)

    def open_output(self):
        path = self.last_root or Path(self.output.text())
        if path.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def about(self):
        QMessageBox.information(self, "版权与许可",
            "SubtitleFlow 不主张用户输入或输出字幕的权利，不对输出另加商用限制、署名要求或水印。\n\n"
            "用户已有内容权利保持不变。商业使用需具备原内容的相应授权，并遵守所选 API 服务条款。\n\n"
            "本软件使用 LGPLv3 的 PySide6 / Qt 动态库。第三方许可、对应源码提供方式与库替换说明见安装目录 legal 文件夹。"
            "软件专有部分采用 XMRayLabs EULA，限制未经许可的复制、转售和修改。")

    def check_update(self, silent=False):
        if self.update_busy or not self.repo.text().strip():
            if not silent and not self.repo.text().strip():
                self.status.setText("尚未配置 GitHub 更新仓库")
            return
        self.update_busy = True
        self.update_button.setEnabled(False)
        def success(release):
            self.update_busy = False
            self.update_button.setEnabled(True)
            if not release:
                if not silent:
                    self.status.setText("当前已是最新版本")
                    self.update_status.setText("当前已是最新版本，或尚无已发布稳定版")
                return
            if QMessageBox.question(self, "发现新版本", f"发现 {release.version}，是否下载并在任务结束后安装？") != QMessageBox.Yes:
                return
            self.update_busy = True
            self.update_cancel.clear()
            self.launch(lambda _: updates.download(release, app_data() / "updates", self.update_cancel),
                        downloaded, failed)
        def downloaded(path):
            self.update_busy = False
            self.pending_update = path
            if self.any_busy():
                self.status.setText("更新已下载并通过校验，将在当前任务结束后提示安装")
            else:
                self.install_update()
        def failed(message):
            self.update_busy = False
            self.update_button.setEnabled(True)
            if not silent:
                self.error(message)
            else:
                self.status.setText("后台更新检查未成功，可稍后手动检查")
        repo = self.repo.text().strip()
        self.launch(lambda _: updates.check(repo), success, failed)

    def install_update(self):
        if not self.pending_update:
            return
        if any(worker.isRunning() for worker in self.workers):
            QTimer.singleShot(200, self.install_update)
            return
        downloaded = self.pending_update
        self.pending_update = None
        if QMessageBox.question(self, "安装更新", "安装包校验通过，现在关闭软件并打开安装包？") == QMessageBox.Yes:
            try:
                path = updates.verify_install(downloaded)
            except Exception as exc:
                self.error(str(exc))
                return
            if QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
                self.close()
            else:
                self.error("无法打开安装包，请从更新下载目录手动安装")

    def closeEvent(self, event):
        self.update_cancel.set()
        if any(worker.isRunning() for worker in self.workers):
            self.cancel.set()
            self.transcribe_page.cancel.set()
            self.status.setText("正在结束后台操作，请稍候再关闭窗口")
            self.transcribe_page.status.setText("正在结束转录，请稍候再关闭窗口")
            event.ignore()
            return
        try:
            self.save_settings()
        except Exception as exc:
            self.error(str(exc))
            event.ignore()
            return
        if self.transcription_service:
            self.transcription_service.stop()
        event.accept()


def main():
    if len(sys.argv) == 3 and sys.argv[1] == '--smoke-test':
        from .smoke import run
        sys.exit(run(sys.argv[2]))
    app = QApplication(sys.argv)
    app.setApplicationName("SubtitleFlow")
    app.setOrganizationName("SubtitleFlow")
    # Show the proprietary license once per version before normal use.
    license_path = Path(__file__).resolve().parent.parent / "legal" / "EULA.md"
    acceptance = app_data() / "eula-accepted.json"
    accepted = False
    try:
        accepted = json.loads(acceptance.read_text(encoding="utf-8")).get("version") == "1.0"
    except (OSError, ValueError, AttributeError):
        pass
    if not accepted:
        from PySide6.QtWidgets import QDialog, QTextBrowser, QDialogButtonBox
        dialog = QDialog()
        dialog.setWindowTitle("SubtitleFlow · 用户许可协议")
        dialog.resize(700, 560)
        layout = QVBoxLayout(dialog)
        browser = QTextBrowser()
        browser.setMarkdown(license_path.read_text(encoding="utf-8"))
        layout.addWidget(browser)
        buttons = QDialogButtonBox()
        agree = buttons.addButton("同意并继续", QDialogButtonBox.AcceptRole)
        buttons.addButton("退出", QDialogButtonBox.RejectRole)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.Accepted:
            return
        acceptance.write_text(json.dumps({"version": "1.0"}), encoding="utf-8")
    window = Window()
    window.show()
    sys.exit(app.exec())
