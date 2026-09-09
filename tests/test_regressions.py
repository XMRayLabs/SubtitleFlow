import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from subtitleflow.api import APIConfig
from subtitleflow.jobs import Job, JobOptions
from subtitleflow.srt import Cue, render
from subtitleflow.updates import check
from tools.release_gate import gate


class RegressionTests(unittest.TestCase):
    def test_resume_uses_original_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "a.srt"
            source.write_text(render([Cue(1, 0, 1000, "Hello.")]), encoding="utf-8")
            args = JobOptions(mode="merge"), APIConfig("", "", "")
            result = Job([source], root / "out", *args, threading.Event()).run()
            report = json.loads((result / "report.json").read_text(encoding="utf-8"))
            report["files"][0]["status"] = "failed"
            (result / "report.json").write_text(json.dumps(report), encoding="utf-8")
            source.unlink()
            Job([], root / "out", *args, threading.Event(), resume=result).run()
            report = json.loads((result / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["files"][0]["status"], "done")

    def test_resume_rejects_unsafe_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = {"settings": {"options": {"mode": "merge", "merge": {"target": 10.0, "silence": 3.0, "tolerance": 25.0},
                                               "batch_size": 20, "ai": False}, "base_url": "", "model": ""},
                      "files": [{"name": "../escape.srt", "source": "missing", "status": "failed"}]}
            (root / "report.json").write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaises(ValueError):
                Job([], root, JobOptions(mode="merge"), APIConfig("", "", ""), threading.Event(), resume=root).run()
            self.assertFalse((root / "原始的srt").exists())

    def test_release_gate_blocks_without_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp)
            (bundle / "app.exe").write_bytes(b"test")
            with self.assertRaises((FileNotFoundError, RuntimeError)):
                gate(bundle, bundle / "no-audit.json")


if __name__ == "__main__":
    unittest.main()
