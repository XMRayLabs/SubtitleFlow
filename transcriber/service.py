"""转录任务队列：逐个处理提交的源文件，按转录分段调用模型。"""
from dataclasses import dataclass, field
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
    status: str = "queued"      # queued / loading / running / done / failed / cancelled
    segment: int = 0            # 当前转录分段序号（从 1 开始）
    segments: int = 0
    position: float = 0.0       # 已转写到的全片时间（秒）
    duration: float = 0.0
    forced: int = 0             # 切点附近没有静音、在目标位置强制切开的次数
    error: str = ""
    engine: str = ""            # 实际使用的引擎、模型与语言，由 submit() 按引擎能力校验后填入
    model: str = ""
    language: str = ""
    cues: list[Cue] = field(default_factory=list)
    cancel: threading.Event = field(default_factory=threading.Event)

    def snapshot(self):
        return {"id": self.id, "status": self.status, "segment": self.segment, "segments": self.segments, "forced": self.forced,
                "position": round(self.position, 2), "duration": round(self.duration, 3), "error": self.error,
                "engine": self.engine, "model": self.model, "language": self.language}


class TranscriptionService:
    """idle_unload_seconds：没有任务这么久后卸载模型、释放显存（服务进程继续运行）。"""

    def __init__(self, engine, media, idle_unload_seconds=600):
        self.engine, self.media = engine, media
        self.idle_unload_seconds = idle_unload_seconds
        self.jobs: dict[str, Job] = {}
        self.pending = queue.Queue()
        self.engine_lock = threading.Lock()   # 处理任务时持有，卸载模型前必须拿到
        self.last_active = time.monotonic()
        threading.Thread(target=self._worker, daemon=True).start()
        threading.Thread(target=self._unload_when_idle, daemon=True).start()

    def engines(self):
        """本服务实际能运行的引擎。只公布装得出来的能力，不预告未实现的引擎。"""
        return [type(self.engine).info()]

    def submit(self, path, segment_seconds, engine=None, model=None, language=None) -> Job:
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
        job = Job(uuid.uuid4().hex, path, float(segment_seconds), engine=info.name,
                  model=model or info.default_model, language=language or info.languages[0])
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
            if not (idle and self.engine.loaded and self.pending.empty()):
                continue
            if self.engine_lock.acquire(blocking=False):
                try:
                    if self.engine.loaded and self.pending.empty():
                        self.engine.unload()
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

                segment_cues = self.engine.transcribe(self.media.clip(audio, segment), on_progress, job.cancel.is_set)
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


def describe_error(exc: BaseException) -> str:
    """错误信息连同底层原因一起返回（第三方库常把真正的原因包在 __cause__ 里）。"""
    messages = []
    while exc is not None and len(messages) < 4:
        text = str(exc) or type(exc).__name__
        if text not in messages:
            messages.append(text)
        exc = exc.__cause__ or exc.__context__
    return "：".join(messages)
