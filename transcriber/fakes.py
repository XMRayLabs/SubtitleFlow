"""测试用的假媒体与假模型：不需要 ffmpeg、GPU 或 torch。

假源文件是一个 JSON 文件，例如 {"duration": 700, "silences": [[299, 301]]}；
可选字段 "fail" 让模型报错，"delay" 让每个转录分段耗时若干秒（用于取消测试）。
经过假增强的分段，字幕文字后面带「（已增强）」。
不是 JSON 的文件按 30 秒、无静音处理，方便用任意文件做界面联调。
"""
import json
from pathlib import Path
import time

from .engines import EngineInfo
from .segments import Cue


class FakeMedia:
    def probe(self, path):
        try:
            spec = json.loads(Path(path).read_text(encoding="utf-8"))
        except (ValueError, UnicodeDecodeError):
            spec = {"duration": 30}
        spec["_count"] = 0
        return spec, float(spec["duration"]), [tuple(s) for s in spec.get("silences", [])]

    def clip(self, spec, segment, enhance=None):
        spec["_count"] += 1
        clip = {**spec, "index": spec["_count"], "length": segment.end - segment.start}
        return enhance(clip) if enhance else clip


class FakeEnhancer:
    """第一次使用前要"下载" download_size 字节，之后直接可用。"""

    def __init__(self, download_size=3):
        self.download_size, self.downloaded, self.loaded = download_size, False, False

    def missing_bytes(self):
        return 0 if self.downloaded else self.download_size

    def download(self, on_progress, should_stop):
        for done in range(1, self.download_size + 1):
            if should_stop():
                return
            on_progress(done)
        self.downloaded = True

    def load(self):
        self.loaded = True

    def unload(self):
        self.loaded = False

    def enhance(self, clip, should_stop):
        return {**clip, "enhanced": True}


class FakeEngine:
    def __init__(self):
        self.loaded = False

    @classmethod
    def info(cls) -> EngineInfo:
        return EngineInfo(name="fake", models=("fake",), default_model="fake",
                          languages=("auto", "zh", "en"), diarization=False)

    def load(self):
        self.loaded = True

    def unload(self):
        self.loaded = False

    def transcribe(self, clip, on_progress, should_stop):
        if clip.get("fail"):
            raise RuntimeError(clip["fail"])
        deadline = time.time() + clip.get("delay", 0)
        while time.time() < deadline:
            if should_stop():
                return []
            time.sleep(0.01)
        length = clip["length"]
        on_progress(length)
        return [Cue(0, round(length * 1000), f"第{clip['index']}段" + ("（已增强）" if clip.get("enhanced") else ""))]
