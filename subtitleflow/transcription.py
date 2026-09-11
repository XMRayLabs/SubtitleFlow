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
    """开发环境：用当前（装有 torch 的）解释器以模块方式运行仓库里的转录服务。"""
    if getattr(sys, "frozen", False) or importlib.util.find_spec("torch") is None:
        return None
    root = Path(__file__).resolve().parent.parent
    if not (root / "transcriber" / "__main__.py").exists():
        return None
    return [sys.executable, "-m", "transcriber"], root


class ServiceClient:
    def __init__(self, base, token, timeout=10):
        self.base, self.token, self.timeout = base, token, timeout

    def request(self, method, path, body=None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.token}")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            try:
                message = json.loads(exc.read()).get("error")
            except ValueError:
                message = None
            raise ServiceError(message or f"转录服务返回错误 {exc.code}")
        except (urllib.error.URLError, OSError):
            raise ServiceError("无法连接转录服务，服务可能已退出")

    def health(self):
        return self.request("GET", "/v1/health")

    def submit(self, path, segment_seconds):
        return self.request("POST", "/v1/jobs", {"path": str(path), "segment_seconds": segment_seconds})

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
            self.stop()
            raise ServiceError("转录服务启动失败")
        # 服务之后的输出（第三方库的提示等）持续读走，避免管道写满卡住服务；读到结尾时关闭管道
        def drain(stream=self.process.stdout):
            with stream:
                stream.read()

        threading.Thread(target=drain, daemon=True).start()
        self.draining = True
        if hello["api_version"] != TRANSCRIBER_API_VERSION:
            self.stop()
            raise IncompatibleService("转录服务版本与当前软件不兼容，需要更新转录服务")
        self.client = ServiceClient(f"http://127.0.0.1:{hello['port']}", hello["token"])

    def stop(self):
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


def output_path(source: Path) -> Path:
    return source.with_suffix(".srt")


class TranscribeJob:
    """逐个转录源文件，每个完成后把 SRT 写到源文件旁边。单个文件失败不影响其他文件。

    event 回调：("file", 序号, 状态, 说明)、("progress", 序号, 0–1)、("complete", done|partial|cancelled)
    """

    def __init__(self, paths, segment_seconds, service: ServiceManager, cancel: threading.Event,
                 event=lambda *args: None, poll_interval=0.3):
        self.paths = [Path(p) for p in paths]
        self.segment_seconds, self.service, self.cancel = segment_seconds, service, cancel
        self.event, self.poll_interval = event, poll_interval

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
            job_id = client.submit(path, self.segment_seconds)["id"]
            while True:
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
            target = output_path(path)
            srt.write(target, client.result(job_id))
            self.event("progress", index, 1.0)
            self.event("file", index, "已完成", str(target))
            return "done"
        except (ServiceError, OSError, ValueError) as exc:
            self.event("file", index, "失败", str(exc))
            return "failed"
