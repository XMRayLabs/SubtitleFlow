"""测试用的假媒体与假模型：不需要 ffmpeg、GPU 或 torch。

假源文件是一个 JSON 文件，例如 {"duration": 700, "silences": [[299, 301]]}；
可选字段 "fail" 让模型报错，"delay" 让每个转录分段耗时若干秒（用于取消测试）。
不是 JSON 的文件按 30 秒、无静音处理，方便用任意文件做界面联调。
"""
import json
from pathlib import Path
import time


class FakeMedia:
    def probe(self, path):
        try:
            spec = json.loads(Path(path).read_text(encoding="utf-8"))
        except (ValueError, UnicodeDecodeError):
            spec = {"duration": 30}
        spec["_count"] = 0
        return spec, float(spec["duration"]), [tuple(s) for s in spec.get("silences", [])]

    def clip(self, spec, segment):
        spec["_count"] += 1
        return {**spec, "index": spec["_count"], "length": segment.end - segment.start}


class FakeEngine:
    def __init__(self):
        self.loaded = False

    def load(self):
        self.loaded = True

    def unload(self):
        self.loaded = False

    def transcribe(self, clip, on_text, should_stop):
        if clip.get("fail"):
            raise RuntimeError(clip["fail"])
        deadline = time.time() + clip.get("delay", 0)
        while time.time() < deadline:
            if should_stop():
                return ""
            time.sleep(0.01)
        raw = f"[0.00][S01] 第{clip['index']}段[{clip['length']:.2f}]"
        on_text(raw)
        return raw
