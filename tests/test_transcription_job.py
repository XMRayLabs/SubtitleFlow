import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest

from subtitleflow import srt
from subtitleflow.transcription import ServiceManager, TranscribeJob

ROOT = Path(__file__).resolve().parent.parent


def fake_service():
    return ServiceManager([sys.executable, "-m", "transcriber", "--engine", "fake"], cwd=ROOT)


class TranscribeJobTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.service = fake_service()
        self.addCleanup(self.service.stop)

    def source(self, name, **spec):
        path = self.dir / name
        path.write_text(json.dumps(spec), encoding="utf-8")
        return path

    def run_job(self, paths, cancel=None):
        events = []
        status = TranscribeJob(paths, 300, self.service, cancel or threading.Event(), lambda *e: events.append(e)).run()
        return status, events

    def test_writes_srt_next_to_source_named_after_it(self):
        source = self.source("采访.mp4", duration=700, silences=[[299, 301]])
        status, events = self.run_job([source])
        self.assertEqual(status, "done")
        cues = srt.read(self.dir / "采访.srt")
        self.assertEqual([(c.start, c.end, c.text) for c in cues], [(0, 300000, "第1段"), (300000, 700000, "第2段")])
        self.assertIn(("file", 0, "已完成", str(self.dir / "采访.srt")), events)

    def test_failed_file_does_not_stop_the_batch(self):
        bad = self.source("bad.wav", duration=60, fail="显存不足")
        good = self.source("good.wav", duration=60)
        status, events = self.run_job([bad, good])
        self.assertEqual(status, "partial")
        self.assertFalse((self.dir / "bad.srt").exists())
        self.assertTrue((self.dir / "good.srt").exists())
        self.assertTrue(any(e[:3] == ("file", 0, "失败") and "显存不足" in e[3] for e in events))


    def test_cancel_mid_file_leaves_no_srt_and_skips_the_rest(self):
        slow = self.source("slow.wav", duration=60, delay=30)
        later = self.source("later.wav", duration=60)
        cancel = threading.Event()
        events = []

        def on_event(*event):
            events.append(event)
            if event[:3] == ("file", 0, "转录中"):
                cancel.set()

        started = time.time()
        status = TranscribeJob([slow, later], 300, self.service, cancel, on_event).run()
        self.assertEqual(status, "cancelled")
        self.assertLess(time.time() - started, 5)
        self.assertFalse((self.dir / "slow.srt").exists())
        self.assertFalse((self.dir / "later.srt").exists())
        self.assertIn(("file", 0, "已取消", ""), events)
        self.assertIn(("file", 1, "未处理", ""), events)


if __name__ == "__main__":
    unittest.main()
