"""转录任务队列：逐个处理提交的源文件，按转录分段调用模型。"""
from dataclasses import dataclass, field
from pathlib import Path
import queue
import threading
import uuid

from .segments import Cue, finalize_cues, latest_timestamp, parse_output, plan_segments

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
    error: str = ""
    cues: list[Cue] = field(default_factory=list)
    cancel: threading.Event = field(default_factory=threading.Event)

    def snapshot(self):
        return {"id": self.id, "status": self.status, "segment": self.segment, "segments": self.segments,
                "position": round(self.position, 2), "duration": round(self.duration, 3), "error": self.error}


class TranscriptionService:
    def __init__(self, engine, media):
        self.engine, self.media = engine, media
        self.jobs: dict[str, Job] = {}
        self.pending = queue.Queue()
        threading.Thread(target=self._worker, daemon=True).start()

    def submit(self, path, segment_seconds) -> Job:
        if not isinstance(path, str) or not Path(path).is_absolute():
            raise ValueError("源文件路径必须是绝对路径")
        if not Path(path).is_file():
            raise ValueError(f"源文件不存在：{path}")
        if not isinstance(segment_seconds, (int, float)) or not MIN_SEGMENT_SECONDS <= segment_seconds <= MAX_SEGMENT_SECONDS:
            raise ValueError(f"分段时长必须在 {MIN_SEGMENT_SECONDS}–{MAX_SEGMENT_SECONDS} 秒之间")
        job = Job(uuid.uuid4().hex, path, float(segment_seconds))
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
            self._process(self.pending.get())

    def _process(self, job: Job):
        if job.cancel.is_set():
            job.status = "cancelled"
            return
        try:
            audio, job.duration, silences = self.media.probe(job.path)
            plan = plan_segments(job.duration, silences, job.segment_seconds)
            job.segments = len(plan)
            if not self.engine.loaded:
                job.status = "loading"
                self.engine.load()
            job.status = "running"
            cues = []
            for index, segment in enumerate(plan, 1):
                if job.cancel.is_set():
                    raise Cancelled()
                job.segment, job.position = index, segment.start

                def on_text(raw, start=segment.start):
                    stamp = latest_timestamp(raw)
                    if stamp is not None:
                        job.position = min(start + stamp, job.duration)

                raw = self.engine.transcribe(self.media.clip(audio, segment), on_text, job.cancel.is_set)
                if job.cancel.is_set():
                    raise Cancelled()
                cues += parse_output(raw, segment.start)
            job.cues = finalize_cues(cues)
            job.position = job.duration
            job.status = "done"
        except Cancelled:
            job.status = "cancelled"
        except Exception as exc:
            job.error = str(exc) or type(exc).__name__
            job.status = "failed"
