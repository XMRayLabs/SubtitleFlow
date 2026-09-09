from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
import hashlib
import json
import shutil
import threading
import uuid
from . import srt, fcpxml
from .safety import safe_tree, read_json, read_bytes, atomic_write, SRT_LIMIT
from .api import Client, APIConfig, Cancelled
from .merge import MergeOptions, merge
from .translate import translate, ai_boundaries, atomic_json


@dataclass(frozen=True)
class JobOptions:
    mode: str = "both"
    merge: MergeOptions = MergeOptions()
    batch_size: int = 20
    ai: bool = False
    export_fcpxml: bool = False
    fps: str = "25"


class Job:
    def __init__(self, paths, output: Path, options: JobOptions, api: APIConfig,
                 cancel: threading.Event, event=lambda *args: None, resume: Path | None = None):
        self.paths = [Path(p).resolve() for p in paths]
        self.output, self.options, self.api = Path(output), options, api
        self.cancel, self.event, self.resume = cancel, event, resume
        self.root = None

    def run(self):
        self.options.merge.validate()
        if self.options.mode not in ("merge", "translate", "both", "fcpxml"):
            raise ValueError("未知处理模式")
        if not 1 <= self.options.batch_size <= 200:
            raise ValueError("每批条目数必须在 1–200 之间")
        needs_api = self.options.mode in ("translate", "both") or (self.options.ai and self.options.mode != "fcpxml")
        if self.options.export_fcpxml or self.options.mode == "fcpxml":
            fcpxml.ExportOptions(fps=self.options.fps).validate()
        if needs_api:
            self.api.endpoint()
        settings = {"options": asdict(self.options), "base_url": self.api.base_url, "model": self.api.model}
        if self.resume:
            self.root = safe_tree(self.resume).resolve()
            report = read_json(self.root / "report.json")
            report["settings"]["options"].setdefault("export_fcpxml", False)
            report["settings"]["options"].setdefault("fps", "25")
            if report["settings"] != settings:
                raise ValueError("恢复任务的参数或模型已改变，请新建任务")
        else:
            self.output.mkdir(parents=True, exist_ok=True)
            self.root = self.output / (datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8])
            self.root.mkdir()
            report = {"settings": settings, "files": [], "status": "running"}
            used = set()
            for path in self.paths:
                stem, count = path.stem, 1
                name = stem + ".srt"
                while name.casefold() in used:
                    count += 1
                    name = f"{stem}_{count}.srt"
                used.add(name.casefold())
                report["files"].append({"source": str(path), "name": name, "status": "pending"})
        safe_tree(self.root)
        if len(report["files"]) > 10000:
            raise ValueError("单个任务最多支持 10000 个文件")
        names = set()
        for item in report["files"]:
            name = item.get("name", "")
            if (not isinstance(name, str) or not name.lower().endswith(".srt")
                    or "/" in name or "\\" in name or ":" in name or name in (".", "..")
                    or name.casefold() in names):
                raise ValueError("任务报告包含不安全或重复的输出文件名")
            names.add(name.casefold())
        for folder in ("originals", ".progress"):
            (self.root / folder).mkdir(exist_ok=True)
        if self.options.mode in ("merge", "both"):
            (self.root / "merged").mkdir(exist_ok=True)
        if self.options.mode in ("translate", "both"):
            (self.root / "translated").mkdir(exist_ok=True)
        if self.options.export_fcpxml or self.options.mode == "fcpxml":
            (self.root / "fcpxml").mkdir(exist_ok=True)
        report["status"] = "running"
        atomic_json(self.root / "report.json", report)
        self.event("root", str(self.root))
        client = Client(self.api, self.cancel)
        for index, item in enumerate(report["files"]):
            if item["status"] == "done":
                self.event("file", index, "已完成", "")
                continue
            if self.cancel.is_set():
                break
            try:
                item.pop("error", None)
                item["status"] = "running"
                atomic_json(self.root / "report.json", report)
                self.event("file", index, "处理中", "")
                original = self.root / "originals" / item["name"]
                safe_tree(self.root)
                if not original.exists():
                    if self.resume:
                        raise ValueError("任务原始副本缺失，请重新添加字幕建立新任务")
                    atomic_write(original, read_bytes(Path(item["source"]), SRT_LIMIT))
                raw_hash = hashlib.sha256(read_bytes(original, SRT_LIMIT)).hexdigest()
                if item.get("sha256") and item["sha256"] != raw_hash:
                    raise ValueError("任务原始副本已被修改，请新建任务")
                item["sha256"] = raw_hash
                cues = srt.read(original)
                if self.options.mode in ("merge", "both"):
                    boundaries = ai_boundaries(cues, client, self.options.batch_size) if self.options.ai else None
                    cues = merge(cues, self.options.merge, boundaries)
                    srt.write(self.root / "merged" / item["name"], cues)
                if self.options.mode in ("translate", "both"):
                    cues = translate(cues, client, self.options.batch_size,
                                     self.root / ".progress" / (item["name"] + ".json"),
                                     lambda done, total: self.event("file", index, "翻译中", f"{done}/{total}"))
                    client.check()
                    srt.write(self.root / "translated" / item["name"], cues)
                if self.options.export_fcpxml or self.options.mode == "fcpxml":
                    client.check()
                    fcpxml.write(self.root / "fcpxml" / (Path(item["name"]).stem + ".fcpxml"), cues, fcpxml.ExportOptions(fps=self.options.fps))
                item["status"] = "done"
                self.event("file", index, "已完成", "")
            except Cancelled:
                item["status"] = "cancelled"
                self.event("file", index, "已取消", "")
                break
            except Exception as exc:
                item["status"] = "failed"
                # Client errors are sanitized; redact key defensively for all other errors.
                message = str(exc)
                if self.api.key:
                    message = message.replace(self.api.key, "[REDACTED]")
                item["error"] = message
                self.event("file", index, "失败", message)
            finally:
                atomic_json(self.root / "report.json", report)
        report["status"] = "cancelled" if self.cancel.is_set() else ("partial" if any(x["status"] != "done" for x in report["files"]) else "done")
        atomic_json(self.root / "report.json", report)
        self.event("complete", report["status"])
        return self.root
