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
from .clean import clean, report as clean_report, summary as clean_summary


@dataclass(frozen=True)
class JobOptions:
    mode: str = "both"
    merge: MergeOptions = MergeOptions()
    batch_size: int = 20
    ai: bool = False
    export_fcpxml: bool = False
    fps: str = "25"
    number_translated: bool = False   # 在翻译后的字幕正文开头加顺序编号，只影响翻译后的 SRT


class Job:
    def __init__(self, paths, output: Path, options: JobOptions, api: APIConfig,
                 cancel: threading.Event, event=lambda *args: None, resume: Path | None = None):
        self.paths = [Path(p).resolve() for p in paths]
        self.output, self.options, self.api = Path(output), options, api
        self.cancel, self.event, self.resume = cancel, event, resume
        self.root = None

    def run(self):
        self.options.merge.validate()
        if self.options.mode not in ("merge", "translate", "both", "fcpxml", "clean"):
            raise ValueError("未知处理模式")
        if not 1 <= self.options.batch_size <= 200:
            raise ValueError("每批条目数必须在 1–200 之间")
        needs_api = self.options.mode in ("translate", "both") or (self.options.ai and self.options.mode != "fcpxml")
        if self.exports_fcpxml():
            fcpxml.ExportOptions(fps=self.options.fps).validate()
        if needs_api:
            self.api.endpoint()
        settings = {"options": asdict(self.options), "base_url": self.api.base_url, "model": self.api.model}
        if self.resume:
            self.root = safe_tree(self.resume).resolve()
            report = read_json(self.root / "report.json")
            report["settings"]["options"].setdefault("export_fcpxml", False)
            report["settings"]["options"].setdefault("fps", "25")
            report["settings"]["options"].setdefault("number_translated", False)
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
        if self.resume:
            # Migrate whole result folders, including completed files; never overwrite a destination.
            for old, new in (("originals", "原始的srt"), ("merged", "合并后的srt"), ("translated", "翻译后的srt"), ("fcpxml", "转换后的fcpxml")):
                previous, destination = self.root / old, self.root / new
                if previous.exists():
                    if destination.exists():
                        raise ValueError("任务中同时存在新旧结果目录，请先整理后恢复")
                    previous.rename(destination)
        for folder in ("原始的srt", ".progress"):
            (self.root / folder).mkdir(exist_ok=True)
        if self.options.mode in ("merge", "both"):
            (self.root / "合并后的srt").mkdir(exist_ok=True)
        if self.options.mode in ("translate", "both"):
            (self.root / "翻译后的srt").mkdir(exist_ok=True)
        if self.options.mode == "clean":
            for folder in ("清理后的srt", "清理报告"):
                (self.root / folder).mkdir(exist_ok=True)
        if self.exports_fcpxml():
            (self.root / "转换后的fcpxml").mkdir(exist_ok=True)
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
                self.log(item["name"], {"type": "start"})
                self.event("file", index, "处理中", "")
                original = self.root / "原始的srt" / item["name"]
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
                outcome = ""
                if self.options.mode == "clean":
                    cleaned, changes = clean(cues)
                    srt.write(self.root / "清理后的srt" / item["name"], cleaned)
                    atomic_write(self.root / "清理报告" / (Path(item["name"]).stem + ".txt"),
                                 clean_report(item["name"], len(cues), changes).encode("utf-8"))
                    outcome = clean_summary(changes)
                    self.log(item["name"], {"type": "cleaned", "removed": sum(c.action == "removed" for c in changes),
                                            "fixed": sum(c.action == "fixed" for c in changes)})
                if self.options.mode in ("merge", "both"):
                    boundaries = None
                    if self.options.ai:
                        self.event("file", index, "AI 断句中", "")
                        on_activity = self.activity(client, index, item["name"], "AI 断句")
                        boundaries = ai_boundaries(cues, client, self.options.batch_size,
                                                   self.stage_progress(index, "AI 断句"), on_activity)
                    cues = merge(cues, self.options.merge, boundaries)
                    srt.write(self.root / "合并后的srt" / item["name"], cues)
                if self.options.mode in ("translate", "both"):
                    self.event("file", index, "翻译中", "")
                    on_activity = self.activity(client, index, item["name"], "翻译")
                    cues = translate(cues, client, self.options.batch_size,
                                     self.root / ".progress" / (item["name"] + ".json"),
                                     self.stage_progress(index, "翻译"), on_activity)
                    client.check()
                    # 编号只写进翻译后的 SRT：FCPXML 标题里不该出现它
                    srt.write(self.root / "翻译后的srt" / item["name"],
                              srt.numbered(cues) if self.options.number_translated else cues)
                if self.exports_fcpxml():
                    client.check()
                    fcpxml.write(self.root / "转换后的fcpxml" / (Path(item["name"]).stem + ".fcpxml"), cues, fcpxml.ExportOptions(fps=self.options.fps))
                item["status"] = "done"
                self.log(item["name"], {"type": "done"})
                self.event("file", index, "已完成", outcome)
            except Cancelled:
                item["status"] = "cancelled"
                self.log(item["name"], {"type": "cancelled"})
                self.event("file", index, "已取消", "")
                break
            except Exception as exc:
                item["status"] = "failed"
                # Client errors are sanitized; redact key defensively for all other errors.
                message = self.redact(str(exc))
                item["error"] = message
                self.log(item["name"], {"type": "failed", "error": type(exc).__name__, "message": message})
                self.event("file", index, "失败", message)
            finally:
                atomic_json(self.root / "report.json", report)
        report["status"] = "cancelled" if self.cancel.is_set() else ("partial" if any(x["status"] != "done" for x in report["files"]) else "done")
        atomic_json(self.root / "report.json", report)
        self.event("complete", report["status"])
        return self.root

    def exports_fcpxml(self):
        # 清理字幕只输出清理后的 SRT；其余方式按勾选导出
        return self.options.mode == "fcpxml" or (self.options.export_fcpxml and self.options.mode != "clean")

    def redact(self, text):
        return text.replace(self.api.key, "[REDACTED]") if self.api.key else text

    def log(self, name, event):
        """追加一行到任务目录的 translate.log（JSON Lines），事后排查哪一批卡住、为什么。
        只记编号、计数、耗时和已脱敏的错误信息，绝不写请求或响应正文。"""
        line = {"time": datetime.now().isoformat(timespec="seconds"), "file": name}
        line.update({k: self.redact(v) if isinstance(v, str) else v for k, v in event.items()})
        try:
            with (self.root / "translate.log").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(line, ensure_ascii=False) + "\n")
        except OSError:
            pass  # 日志只是辅助排查，写不进去不该让翻译本身失败

    def activity(self, client, index, name, stage):
        """把 Client 的请求/重试事件和 translate 的批次事件合成一路：写日志，并转给界面。"""
        def report(event):
            event = {**event, "stage": stage}
            self.log(name, {k: v for k, v in event.items() if k != "preview"})
            self.event("activity", index, event)
        client.on_event = report
        return report

    def stage_progress(self, index, stage):
        return lambda done, total: self.event("progress", index, stage, done, total)
