"""音频转录：管理本机转录服务进程，并通过它的 HTTP 接口转录源文件（见 ADR-0001）。

主程序只和服务的接口打交道，不 import torch 或模型代码。
"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

from . import srt
from .safety import response_json

# 主程序支持的转录服务接口版本；与服务端 transcriber.API_VERSION 对应
TRANSCRIBER_API_VERSION = 1
AUDIO_SUFFIXES = {".mp3", ".wav", ".m4a", ".flac", ".aac", ".ogg", ".opus", ".wma"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".flv", ".wmv"}
MEDIA_SUFFIXES = AUDIO_SUFFIXES | VIDEO_SUFFIXES
FINISHED = ("done", "failed", "cancelled")
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class ServiceError(Exception):
    pass


class IncompatibleService(ServiceError):
    """转录服务的接口版本与主程序不兼容，需要更新转录服务。"""


def is_media(path) -> bool:
    return Path(path).suffix.lower() in MEDIA_SUFFIXES


def development_command():
    """开发环境：以模块方式运行仓库里的转录服务。解释器可用环境变量 SUBTITLEFLOW_TRANSCRIBER_PYTHON
    指定（装有 torch 的环境），否则使用当前解释器（需已装 torch）。"""
    if getattr(sys, "frozen", False):
        return None
    root = Path(__file__).resolve().parent.parent
    if not (root / "transcriber" / "__main__.py").exists():
        return None
    python = os.environ.get("SUBTITLEFLOW_TRANSCRIBER_PYTHON")
    if python:
        return [python, "-m", "transcriber"], root
    if importlib.util.find_spec("torch") is None:
        return None
    return [sys.executable, "-m", "transcriber"], root


class ServiceClient:
    def __init__(self, base, token, timeout=50):
        self.base, self.token, self.timeout = base, token, timeout

    def request(self, method, path, body=None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.token}")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return response_json(resp)
        except urllib.error.HTTPError as exc:
            try:
                message = response_json(exc).get("error")
            except (ValueError, AttributeError):
                message = None
            raise ServiceError(message or f"转录服务返回错误 {exc.code}")
        except (urllib.error.URLError, OSError) as exc:
            cause = exc.reason if isinstance(exc, urllib.error.URLError) else exc
            raise ServiceError(f"无法连接转录服务，服务可能已退出（{method} {path}：{cause!r}）")

    def health(self):
        return self.request("GET", "/v1/health")

    def engines(self):
        """服务实际能运行的引擎；界面据此渲染选项，不内置各引擎的知识。"""
        return self.request("GET", "/v1/engines")["engines"]

    def submit(self, path, segment_seconds, engine=None, model=None, language=None):
        # 省略的参数不发给服务，由引擎取默认值；发出去的值服务会校验，无效时返回 400
        body = {"path": str(path), "segment_seconds": segment_seconds}
        body.update({k: v for k, v in (("engine", engine), ("model", model), ("language", language)) if v})
        return self.request("POST", "/v1/jobs", body)

    def status(self, job_id):
        return self.request("GET", f"/v1/jobs/{job_id}")

    def result(self, job_id):
        return [srt.Cue(i, c["start"], c["end"], c["text"])
                for i, c in enumerate(self.request("GET", f"/v1/jobs/{job_id}/result")["cues"], 1)]

    def cancel(self, job_id):
        return self.request("POST", f"/v1/jobs/{job_id}/cancel", {})


class ServiceManager:
    """本机转录服务进程：按需启动，读取握手信息（端口、令牌、接口版本）。"""

    def __init__(self, command, cwd=None, startup_timeout=60):
        self.command, self.cwd, self.startup_timeout = list(command), cwd, startup_timeout
        self.process = None
        self.client = None
        self.draining = False
        self.lock = threading.Lock()

    def alive(self):
        return self.process is not None and self.process.poll() is None

    def ensure(self) -> ServiceClient:
        with self.lock:
            if not self.alive():
                self._start()
            return self.client

    def _start(self):
        self.client = None
        self.draining = False
        command = self.command + ["--parent-pid", str(os.getpid())]
        try:
            self.process = subprocess.Popen(command, cwd=self.cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                            stderr=subprocess.DEVNULL, creationflags=_NO_WINDOW)
        except OSError as exc:
            raise ServiceError(f"无法启动转录服务：{exc}")
        hello = {}

        def handshake():
            line = self.process.stdout.readline()
            try:
                hello.update(json.loads(line))
            except ValueError:
                pass

        reader = threading.Thread(target=handshake, daemon=True)
        reader.start()
        reader.join(self.startup_timeout)
        if not {"port", "token", "api_version"} <= hello.keys():
            self._stop()
            raise ServiceError("转录服务启动失败")
        # 服务之后的输出（第三方库的提示等）持续读走，避免管道写满卡住服务；读到结尾时关闭管道
        def drain(stream=self.process.stdout):
            with stream:
                stream.read()

        threading.Thread(target=drain, daemon=True).start()
        self.draining = True
        if hello["api_version"] != TRANSCRIBER_API_VERSION:
            self._stop()
            raise IncompatibleService("转录服务版本与当前软件不兼容，需要更新转录服务")
        self.client = ServiceClient(f"http://127.0.0.1:{hello['port']}", hello["token"])

    def stop(self):
        # 与 ensure() 共用锁：避免后台预热正在启动服务时被卸载或关闭窗口打断到一半
        with self.lock:
            self._stop()

    def _stop(self):
        process, self.process, self.client = self.process, None, None
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(5)
        if process and not self.draining:
            process.stdout.close()


def fmt_time(sec) -> str:
    sec = int(sec)
    return f"{sec // 3600}:{sec // 60 % 60:02d}:{sec % 60:02d}" if sec >= 3600 else f"{sec // 60:02d}:{sec % 60:02d}"


def describe(status) -> str:
    """把服务返回的任务状态变成"进度·说明"一栏的文字。"""
    if status["status"] == "queued":
        return "排队中"
    if status["status"] == "loading":
        return "正在加载模型…"
    if status["status"] == "running":
        return (f"分段 {status['segment']}/{status['segments']} · "
                f"已转写到 {fmt_time(status['position'])} / {fmt_time(status['duration'])}")
    return status.get("error", "")


def default_fallback_dirs() -> list[Path]:
    """源文件目录写不进去时依次尝试的保存位置。不用程序安装目录：Program Files 对普通用户不可写。"""
    local = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    return [Path.home() / "SubtitleFlow输出" / "转录", Path(local) / "SubtitleFlow" / "转录输出"]


def reserve_name(directory: Path, stem: str) -> Path:
    """同名 SRT 已存在时依次改名为 `名称 (1).srt`、`名称 (2).srt`……，从不覆盖已有文件。
    用独占创建的占位文件确定名字，避免"检查不存在"与写入之间被其他程序抢先创建同名文件。"""
    count = 0
    while True:
        target = directory / (f"{stem}.srt" if count == 0 else f"{stem} ({count}).srt")
        try:
            target.open("x").close()
            return target
        except FileExistsError:
            count += 1


def save_srt(source: Path, cues, fallback_dirs) -> Path:
    """保存到源文件旁；写不进去时依次改存到 fallback_dirs。返回实际保存路径。"""
    error = None
    for index, directory in enumerate([source.parent, *fallback_dirs]):
        try:
            if index:
                directory.mkdir(parents=True, exist_ok=True)
            target = reserve_name(directory, source.stem)
            try:
                srt.write(target, cues)
            except BaseException:
                target.unlink(missing_ok=True)
                raise
            return target
        except (OSError, ValueError) as exc:
            error = exc
    raise OSError(f"无法保存 SRT：{error}")


class TranscribeJob:
    """逐个转录源文件，每个完成后把 SRT 写到源文件旁边。单个文件失败不影响其他文件。

    event 回调：("file", 序号, 状态, 说明)、("progress", 序号, 0–1)、("complete", done|partial|cancelled)
    """

    def __init__(self, paths, segment_seconds, service: ServiceManager, cancel: threading.Event,
                 event=lambda *args: None, poll_interval=0.3, fallback_dirs=None,
                 engine=None, model=None, language=None):
        self.paths = [Path(p) for p in paths]
        self.segment_seconds, self.service, self.cancel = segment_seconds, service, cancel
        self.event, self.poll_interval = event, poll_interval
        self.engine, self.model, self.language = engine, model, language
        self.fallback_dirs = default_fallback_dirs() if fallback_dirs is None else fallback_dirs

    def run(self):
        results = []
        for index, path in enumerate(self.paths):
            if self.cancel.is_set():
                self.event("file", index, "未处理", "")
                results.append("cancelled")
                continue
            results.append(self._transcribe(index, path))
        status = "cancelled" if self.cancel.is_set() else ("done" if all(r == "done" for r in results) else "partial")
        self.event("complete", status)
        return status

    def _transcribe(self, index, path):
        try:
            self.event("file", index, "处理中", "正在启动转录服务…")
            client = self.service.ensure()
            job_id = client.submit(path, self.segment_seconds, self.engine, self.model, self.language)["id"]
            cancel_sent = False
            while True:
                if self.cancel.is_set() and not cancel_sent:
                    client.cancel(job_id)
                    cancel_sent = True
                status = client.status(job_id)
                if status["status"] in FINISHED:
                    break
                self.event("file", index, "转录中", describe(status))
                if status["duration"]:
                    self.event("progress", index, status["position"] / status["duration"])
                time.sleep(self.poll_interval)
            if status["status"] == "failed":
                self.event("file", index, "失败", status["error"])
                return "failed"
            if status["status"] == "cancelled":
                self.event("file", index, "已取消", "")
                return "cancelled"
            target = save_srt(path, client.result(job_id), self.fallback_dirs)
            self.event("progress", index, 1.0)
            self.event("file", index, "已完成", str(target))
            if status.get("forced"):
                self.event("warning", index, f"有 {status['forced']} 处切点附近没有静音，字幕可能在句子中间断开")
            return "done"
        except IncompatibleService as exc:
            self.event("incompatible")
            self.event("file", index, "失败", str(exc))
            return "failed"
        except (ServiceError, OSError, ValueError, KeyError, TypeError) as exc:
            # KeyError / TypeError：服务返回的内容缺字段或格式不对，按失败处理，不中断整批
            message = str(exc) if not isinstance(exc, (KeyError, TypeError)) else "转录服务返回了无法识别的结果"
            if isinstance(exc, ServiceError) and not self.service.alive():
                message = "转录服务意外退出，下次转录时会自动重启"
            self.event("file", index, "失败", message)
            return "failed"
