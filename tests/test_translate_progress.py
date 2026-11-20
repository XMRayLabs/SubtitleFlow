"""翻译进度可见性：批次/重试/拆批事件、translate.log，以及表格每行进度条的显示。"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from subtitleflow.api import APIConfig, Client, APIError, InvalidResponse
from subtitleflow.jobs import Job, JobOptions
from subtitleflow.progress_view import RowProgress, overall
from subtitleflow.safety import ResponseTimeout
from subtitleflow.srt import Cue, render
from subtitleflow.translate import translate, ai_boundaries


def cue(i, text="x"):
    return Cue(i + 1, i * 1000, i * 1000 + 900, text)


class ScriptedClient:
    """按顺序给出预设回答；不带 on_event，确认 translate 只依赖自己的 report 参数。"""

    def __init__(self, config=None, cancel=None):
        self.config = config or APIConfig("https://example.test/v1", "secret", "test")
        self.cancel = cancel or threading.Event()
        self.bad_once = False
        self.maximum = 100

    def check(self):
        pass

    def json_chat(self, system, content):
        values = json.loads(content)
        from subtitleflow.api import TooLarge
        if len(values) > self.maximum:
            raise TooLarge()
        if self.bad_once:
            self.bad_once = False
            return "乱码"
        if system.startswith("Identify"):
            return [values[-1]["id"]]
        return [{"id": x["id"], "text": "译文"} for x in values]


class TranslateEventTests(unittest.TestCase):
    def run_translate(self, client, count, batch):
        events = []
        with tempfile.TemporaryDirectory() as tmp:
            translate([cue(i) for i in range(count)], client, batch, Path(tmp) / "c.json", report=events.append)
        return events

    def test_batches_are_numbered_against_the_remaining_work(self):
        events = self.run_translate(ScriptedClient(), 5, 2)
        batches = [e for e in events if e["type"] == "batch"]
        self.assertEqual([(e["batch"], e["batches"], e["first"], e["last"]) for e in batches],
                         [(1, 3, 0, 1), (2, 3, 2, 3), (3, 3, 4, 4)])

    def test_garbled_answer_is_reported_before_the_retry(self):
        client = ScriptedClient()
        client.bad_once = True
        events = self.run_translate(client, 2, 2)
        kinds = [e["type"] for e in events]
        self.assertEqual(kinds, ["batch", "invalid"])
        self.assertIn("数量不匹配", events[1]["message"])

    def test_split_reports_both_halves(self):
        client = ScriptedClient()
        client.maximum = 2
        events = self.run_translate(client, 4, 4)
        self.assertIn({"type": "split", "sizes": [2, 2]}, events)

    def test_ai_boundaries_reports_batches_and_progress(self):
        events, progress = [], []
        ai_boundaries([cue(i) for i in range(5)], ScriptedClient(), 2,
                      progress=lambda done, total: progress.append((done, total)), report=events.append)
        self.assertEqual([(e["batch"], e["batches"]) for e in events if e["type"] == "batch"], [(1, 3), (2, 3), (3, 3)])
        self.assertEqual(progress[-1], (5, 5))


def chat_response(text="OK"):
    return io.BytesIO(json.dumps({"choices": [{"message": {"content": text}}]}).encode())


class ClientEventTests(unittest.TestCase):
    def client(self):
        client = Client(APIConfig("https://host/v1", "TOP_SECRET", "m"), threading.Event())
        client.events = []
        client.on_event = client.events.append
        return client

    def test_retry_after_http_503_is_reported(self):
        import urllib.error
        client = self.client()
        error = urllib.error.HTTPError("https://host", 503, "busy", {}, io.BytesIO(b""))
        with patch("urllib.request.OpenerDirector.open", side_effect=[error, chat_response()]), \
                patch.object(client.cancel, "wait", return_value=False):
            self.assertEqual(client.chat("s", "u"), "OK")
        kinds = [(e["type"], e["attempt"]) for e in client.events]
        self.assertEqual(kinds, [("request", 1), ("retry", 1), ("request", 2), ("answer", 2)])
        self.assertEqual(client.events[1]["reason"], "HTTP 503")
        self.assertEqual(client.events[1]["delay"], 1)

    def test_slow_body_is_a_timeout_not_a_format_error(self):
        client = self.client()
        responses = [ResponseTimeout("响应超过总读取时限"), {"choices": [{"message": {"content": "OK"}}]}]
        with patch("urllib.request.OpenerDirector.open", side_effect=lambda *a, **k: io.BytesIO(b"")), \
                patch("subtitleflow.api.response_json", side_effect=responses), \
                patch.object(client.cancel, "wait", return_value=False):
            self.assertEqual(client.chat("s", "u"), "OK")
        self.assertIn("超时", client.events[1]["reason"])

    def test_repeated_slow_bodies_fail_as_timeout(self):
        client = self.client()
        with patch("urllib.request.OpenerDirector.open", side_effect=lambda *a, **k: io.BytesIO(b"")), \
                patch("subtitleflow.api.response_json", side_effect=ResponseTimeout("响应超过总读取时限")), \
                patch.object(client.cancel, "wait", return_value=False):
            with self.assertRaises(APIError) as caught:
                client.chat("s", "u")
        self.assertNotIsInstance(caught.exception, InvalidResponse)
        self.assertIn("超时", str(caught.exception))


class JobLogTests(unittest.TestCase):
    def test_events_reach_the_gui_and_translate_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "a.srt"
            source.write_text(render([cue(i, "Hello.") for i in range(3)]), encoding="utf-8")
            events = []
            with patch("subtitleflow.jobs.Client", ScriptedClient):
                output = Job([source], root / "out", JobOptions(mode="translate", batch_size=2),
                             APIConfig("https://example.test/v1", "secret", "test"), threading.Event(),
                             lambda *e: events.append(e)).run()
            self.assertIn(("progress", 0, "翻译", 3, 3), events)
            activity = [e[2] for e in events if e[0] == "activity"]
            self.assertEqual([(a["stage"], a["batch"]) for a in activity if a["type"] == "batch"], [("翻译", 1), ("翻译", 2)])
            text = (output / "translate.log").read_text(encoding="utf-8")
            self.assertNotIn("secret", text)
            lines = [json.loads(line) for line in text.splitlines()]
            self.assertEqual(lines[0]["type"], "start")
            self.assertEqual(lines[-1]["type"], "done")
            self.assertTrue(all(line["file"] == "a.srt" and "time" in line for line in lines))
            self.assertNotIn("Hello", text)

    def test_failure_is_logged_with_its_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "a.srt"
            source.write_text(render([cue(0, "Hello.")]), encoding="utf-8")

            class Broken(ScriptedClient):
                def json_chat(self, system, content):
                    raise APIError("API 临时故障，重试 3 次后仍未成功")
            with patch("subtitleflow.jobs.Client", Broken):
                output = Job([source], root / "out", JobOptions(mode="translate"),
                             APIConfig("https://example.test/v1", "secret", "test"), threading.Event()).run()
            last = json.loads((output / "translate.log").read_text(encoding="utf-8").splitlines()[-1])
            self.assertEqual((last["type"], last["message"]), ("failed", "API 临时故障，重试 3 次后仍未成功"))


class RowProgressTests(unittest.TestCase):
    def test_waiting_request_counts_seconds(self):
        row = RowProgress()
        row.progress("翻译", 40, 400)
        row.apply({"type": "batch", "batch": 3, "batches": 20}, now=0)
        row.apply({"type": "request", "attempt": 1}, now=100)
        self.assertEqual(row.text(now=142), "翻译 40/400 · 第 3/20 批 · 请求中 42s")
        self.assertEqual(row.color(), "#5264e8")
        self.assertAlmostEqual(row.fraction(), 0.1)

    def test_retry_turns_amber_and_explains_why(self):
        row = RowProgress()
        row.progress("翻译", 0, 10)
        row.apply({"type": "batch", "batch": 1, "batches": 1}, now=0)
        row.apply({"type": "request", "attempt": 1}, now=0)
        row.apply({"type": "retry", "attempt": 1, "reason": "请求超时", "delay": 2}, now=60)
        self.assertEqual(row.text(now=61), "翻译 0/10 · 第 1/1 批 · 第 1 次重试：请求超时（2s 后）")
        self.assertEqual(row.color(), "#b7791f")
        row.apply({"type": "request", "attempt": 2}, now=62)
        self.assertEqual(row.text(now=67), "翻译 0/10 · 第 1/1 批 · 请求中 5s（第 2 次尝试）")
        self.assertEqual(row.color(), "#b7791f")

    def test_garbled_and_split_notes(self):
        row = RowProgress()
        row.apply({"type": "invalid", "message": "译文条目数量不匹配"}, now=0)
        self.assertEqual(row.text(now=0), "译文格式错误，重试中：译文条目数量不匹配")
        row.apply({"type": "split", "sizes": [10, 10]}, now=0)
        self.assertEqual(row.text(now=0), "输出过长，拆分为 10+10")

    def test_failure_keeps_partial_length(self):
        row = RowProgress()
        row.progress("翻译", 30, 100)
        row.finish("failed", "无法连接 API")
        self.assertEqual((row.text(), row.color()), ("无法连接 API", "#d14d61"))
        self.assertAlmostEqual(row.fraction(), 0.3)

    def test_done_fills_bar(self):
        row = RowProgress()
        row.finish("done")
        self.assertEqual((row.fraction(), row.color()), (1.0, "#16856b"))

    def test_overall_counts_finished_files_plus_current_fraction(self):
        rows = [RowProgress(), RowProgress(), RowProgress(), RowProgress()]
        rows[0].finish("done")
        rows[1].finish("failed", "x")
        rows[2].start()
        rows[2].progress("翻译", 50, 100)
        self.assertAlmostEqual(overall(rows), 2.5 / 4)


class WindowProgressTests(unittest.TestCase):
    def setUp(self):
        from test_gui_pages import WindowTestCase
        from PySide6.QtWidgets import QApplication
        QApplication.instance() or QApplication([])
        helper = WindowTestCase()
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        from subtitleflow import gui
        self.window = gui.Window()
        self.addCleanup(self.window.deleteLater)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        paths = []
        for name in ("a.srt", "b.srt"):
            path = Path(self.tmp.name) / name
            path.write_text(render([cue(0)]), encoding="utf-8")
            paths.append(str(path))
        self.window.add_files(paths)

    def test_events_drive_row_bar_overall_bar_and_status(self):
        from subtitleflow.progress_view import ProgressDelegate
        w = self.window
        self.assertIsInstance(w.table.itemDelegateForColumn(2), ProgressDelegate)
        w.job_event(("file", 0, "已完成", ""))
        w.job_event(("file", 1, "翻译中", ""))
        w.job_event(("progress", 1, "翻译", 20, 40))
        w.job_event(("activity", 1, {"type": "batch", "batch": 2, "batches": 2, "stage": "翻译"}))
        w.job_event(("activity", 1, {"type": "retry", "attempt": 1, "reason": "HTTP 503", "delay": 1, "stage": "翻译"}))
        item = w.table.item(1, 2)
        self.assertIn("第 2/2 批", item.text())
        self.assertEqual(item.data(ProgressDelegate.ROLE), (0.5, "#b7791f"))
        self.assertEqual(w.progress.value(), 750)
        self.assertEqual(w.progress_label.text(), "1/2 个文件 · 75%")
        self.assertIn("b.srt", w.status.text())
        self.assertIn("HTTP 503", w.status.text())

    def test_row_is_painted_without_errors(self):
        w = self.window
        w.resize(1080, 760)
        w.job_event(("progress", 0, "翻译", 1, 4))
        image = w.table.grab()
        self.assertFalse(image.isNull())


if __name__ == "__main__":
    unittest.main()
