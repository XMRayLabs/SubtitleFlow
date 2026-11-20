"""转录任务队列：逐个处理提交的源文件，按转录分段调用模型。"""
from dataclasses import dataclass, field, replace
from pathlib import Path
import queue
import threading
import time
import uuid

from .segments import Cue, finalize_cues, plan_segments, shift_cues

MIN_SEGMENT_SECONDS = 60
MAX_SEGMENT_SECONDS = 600
FINISHED = ("done", "failed", "cancelled")


class Cancelled(Exception):
    pass


@dataclass
class Job:
    id: str
    path: str
    segment_seconds: float
    status: str = "queued"      # queued / downloading / loading / running / done / failed / cancelled
    segment: int = 0            # 当前转录分段序号（从 1 开始）
    segments: int = 0
    position: float = 0.0       # 已转写到的全片时间（秒）
    duration: float = 0.0
    forced: int = 0             # 切点附近没有静音、在目标位置强制切开的次数
    error: str = ""
    engine: str = ""            # 实际使用的引擎、模型与语言，由 submit() 按引擎能力校验后填入
    model: str = ""
    language: str = ""
    enhance: bool = False       # 送进模型前先降噪、去混响
    enhancing: bool = False     # 正在对当前转录分段降噪、去混响
    downloaded: int = 0         # status 为 downloading 时：增强模型已下载 / 共需下载的字节
    download_size: int = 0
    cues: list[Cue] = field(default_factory=list)
    cancel: threading.Event = field(default_factory=threading.Event)

    def snapshot(self):
        return {"id": self.id, "status": self.status, "segment": self.segment, "segments": self.segments, "forced": self.forced,
                "position": round(self.position, 2), "duration": round(self.duration, 3), "error": self.error,
                "engine": self.engine, "model": self.model, "language": self.language, "enhance": self.enhance,
                "enhancing": self.enhancing, "downloaded": self.downloaded, "download_size": self.download_size}


class TranscriptionService:
    """idle_unload_seconds：没有任务这么久后卸载模型、释放显存（服务进程继续运行）。
    enhancer：降噪与去混响（见 enhance.py）；为 None 时不提供这项能力。"""

    def __init__(self, engine, media, idle_unload_seconds=600, enhancer=None):
        self.engine, self.media, self.enhancer = engine, media, enhancer
        self.idle_unload_seconds = idle_unload_seconds
        self.jobs: dict[str, Job] = {}
        self.pending = queue.Queue()
        self.engine_lock = threading.Lock()   # 处理任务时持有，卸载模型前必须拿到
        self.last_active = time.monotonic()
        threading.Thread(target=self._worker, daemon=True).start()
        threading.Thread(target=self._unload_when_idle, daemon=True).start()

    def engines(self):
        """本服务实际能运行的引擎。只公布装得出来的能力，不预告未实现的引擎。"""
        return [replace(type(self.engine).info(), enhance=self.enhancer is not None)]

    def submit(self, path, segment_seconds, engine=None, model=None, language=None, enhance=None) -> Job:
        if not isinstance(path, str) or not Path(path).is_absolute():
            raise ValueError("源文件路径必须是绝对路径")
        if not Path(path).is_file():
            raise ValueError(f"源文件不存在：{path}")
        if not isinstance(segment_seconds, (int, float)) or not MIN_SEGMENT_SECONDS <= segment_seconds <= MAX_SEGMENT_SECONDS:
            raise ValueError(f"分段时长必须在 {MIN_SEGMENT_SECONDS}–{MAX_SEGMENT_SECONDS} 秒之间")
        # 未指定时用引擎默认值；指定了就必须在引擎声明的能力之内，绝不静默忽略
        info = type(self.engine).info()
        if engine is not None and engine != info.name:
            raise ValueError(f"本服务未提供引擎 {engine}，可用：{info.name}")
        if model is not None and model not in info.models:
            raise ValueError(f"引擎 {info.name} 没有模型 {model}，可用：{'、'.join(info.models)}")
        if language is not None and language not in info.languages:
            raise ValueError(f"引擎 {info.name} 不支持语言 {language}，可用：{'、'.join(info.languages)}")
        if enhance is not None and not isinstance(enhance, bool):
            raise ValueError("enhance 必须是 true 或 false")
        if enhance and self.enhancer is None:
            raise ValueError("本服务不支持降噪与去混响")
        job = Job(uuid.uuid4().hex, path, float(segment_seconds), engine=info.name,
                  model=model or info.default_model, language=language or info.languages[0], enhance=bool(enhance))
        self.jobs[job.id] = job
        self.pending.put(job)
        return job

    def get(self, job_id) -> Job | None:
        return self.jobs.get(job_id)

    def cancel(self, job_id) -> Job | None:
        """立即生效：排队中的任务直接取消；进行中的任务在模型生成的下一步停止。"""
        job = self.jobs.get(job_id)
        if job and job.status not in FINISHED:
            job.cancel.set()
            if job.status == "queued":
                job.status = "cancelled"
        return job

    def health(self):
        return {"model_loaded": self.engine.loaded}

    def _models(self):
        return [self.engine] + ([self.enhancer] if self.enhancer is not None else [])

    def _worker(self):
        while True:
            job = self.pending.get()
            with self.engine_lock:
                self._process(job)
                self.last_active = time.monotonic()

    def _unload_when_idle(self):
        while True:
            time.sleep(min(1.0, self.idle_unload_seconds / 3))
            idle = time.monotonic() - self.last_active >= self.idle_unload_seconds
            if not (idle and any(m.loaded for m in self._models()) and self.pending.empty()):
                continue
            if self.engine_lock.acquire(blocking=False):
                try:
                    for model in self._models():
                        if model.loaded and self.pending.empty():
                            model.unload()
                finally:
                    self.engine_lock.release()

    def _process(self, job: Job):
        if job.cancel.is_set():
            job.status = "cancelled"
            return
        try:
            audio, job.duration, silences = self.media.probe(job.path)
            plan = plan_segments(job.duration, silences, job.segment_seconds)
            job.segments = len(plan)
            job.forced = sum(segment.forced for segment in plan)
            if job.enhance:
                self._prepare_enhancer(job)
            if not self.engine.loaded:
                job.status = "loading"
                self.engine.load()
            job.status = "running"
            cues = []
            for index, segment in enumerate(plan, 1):
                if job.cancel.is_set():
                    raise Cancelled()
                job.segment, job.position = index, segment.start

                def on_progress(seconds, start=segment.start):
                    job.position = min(start + seconds, job.duration)

                if job.enhance:
                    job.enhancing = True
                    clip = self.media.clip(audio, segment, lambda hifi: self.enhancer.enhance(hifi, job.cancel.is_set))
                    job.enhancing = False
                    if job.cancel.is_set():
                        raise Cancelled()
                else:
                    clip = self.media.clip(audio, segment)
                segment_cues = self.engine.transcribe(clip, on_progress, job.cancel.is_set)
                if job.cancel.is_set():
                    raise Cancelled()
                cues += shift_cues(segment_cues, segment.start)
            job.cues = finalize_cues(cues)
            job.position = job.duration
            job.status = "done"
        except Cancelled:
            job.status = "cancelled"
        except Exception as exc:
            job.error = describe_error(exc)
            job.status = "failed"
        finally:
            job.enhancing = False

    def _prepare_enhancer(self, job: Job):
        """第一次用到时下载增强模型（中途取消的下次续传），再加载到显卡上。"""
        missing = self.enhancer.missing_bytes()
        if missing:
            job.status, job.download_size = "downloading", missing

            def on_progress(done):
                job.downloaded = done

            self.enhancer.download(on_progress, job.cancel.is_set)
            if job.cancel.is_set():
                raise Cancelled()
        if not self.enhancer.loaded:
            job.status = "loading"
            self.enhancer.load()


def describe_error(exc: BaseException) -> str:
    """错误信息连同底层原因一起返回（第三方库常把真正的原因包在 __cause__ 里）。"""
    messages = []
    while exc is not None and len(messages) < 4:
        text = str(exc) or type(exc).__name__
        if text not in messages:
            messages.append(text)
        exc = exc.__cause__ or exc.__context__
    return "：".join(messages)
