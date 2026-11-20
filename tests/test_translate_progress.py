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

    def test_row_and_status_show_which_cues_are_in_flight(self):
        events = self.run_translate(ScriptedClient(), 5, 2)
        row = RowProgress()
        row.start()
        row.progress("翻译", 0, 5)
        row.apply([e for e in events if e["type"] == "batch"][1], now=0)
        self.assertEqual(row.text(now=0), "翻译 0/5")          # 单元格只留进度
        self.assertIn("第 2/3 批，第 3–4 条", row.detail(now=0))
        self.assertIn("已完成 0/5 条", row.detail(now=0))
        self.assertTrue(row.current().startswith("第 3–4 条（00:00:"))
        self.assertIn(cue(2).text, row.current())

    def test_garbled_answer_is_reported_before_the_retry(self):
        client = ScriptedClient()
        client.bad_once = True
        events = self.run_translate(client, 2, 2)
        kinds = [e["type"] for e in events]
        self.assertEqual(kinds, ["batch", "invalid"])
        self.assertIn("数量不匹配", events[1]["message"])

    def test_a_cue_that_keeps_failing_is_isolated_and_left_untranslated(self):
        class Stubborn(ScriptedClient):
            def json_chat(self, system, content):
                values = json.loads(content)
                if any(v["id"] == 3 for v in values):
                    return [{"id": v["id"], "text": "译文"} for v in values if v["id"] != 3]   # 总是漏掉第 3 条
                return super().json_chat(system, content)
        events = []
        cues = [cue(i, f"line {i}") for i in range(40)]
        with tempfile.TemporaryDirectory() as tmp:
            result = translate(cues, Stubborn(), 8, Path(tmp) / "c.json", report=events.append)
        self.assertEqual(result[3].text, "line 3")
        self.assertTrue(all(c.text == "译文" for i, c in enumerate(result) if i != 3))
        kept = [e for e in events if e["type"] == "kept"]
        self.assertEqual([e["position"] for e in kept], [3])
        self.assertIn("数量不匹配", kept[0]["message"])

    def test_a_batch_that_times_out_is_split_into_shorter_requests(self):
        from subtitleflow.api import RequestTimeout

        class Slow(ScriptedClient):
            def json_chat(self, system, content):
                if len(json.loads(content)) > 5:
                    raise RequestTimeout("模型响应超时")
                return super().json_chat(system, content)
        events = []
        with tempfile.TemporaryDirectory() as tmp:
            result = translate([cue(i) for i in range(20)], Slow(), 20, Path(tmp) / "c.json", report=events.append)
        self.assertTrue(all(c.text == "译文" for c in result))
        self.assertIn({"type": "split", "sizes": [10, 10], "reason": "timeout"}, events)

    def test_ai_boundaries_also_split_on_timeout(self):
        from subtitleflow.api import RequestTimeout

        class Slow(ScriptedClient):
            def json_chat(self, system, content):
                if len(json.loads(content)) > 8:
                    raise RequestTimeout("模型响应超时")
                return super().json_chat(system, content)
        events = []
        ai_boundaries([cue(i) for i in range(10)], Slow(), 10, report=events.append)
        self.assertTrue(any(e.get("reason") == "timeout" for e in events if e["type"] == "split"))

    def test_a_model_that_times_out_on_everything_still_fails_the_file(self):
        from subtitleflow.api import RequestTimeout

        class Stuck(ScriptedClient):
            def json_chat(self, system, content):
                raise RequestTimeout("模型响应超时")
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RequestTimeout, "始终无法翻译"):
                translate([cue(i) for i in range(4)], Stuck(), 4, Path(tmp) / "c.json")

    def test_a_cue_the_model_loops_on_keeps_its_original_text(self):
        from subtitleflow.api import RequestTimeout, TooLarge
        for error in (TooLarge, RequestTimeout):
            class Looping(ScriptedClient):
                def json_chat(self, system, content):
                    if any(v["id"] == 5 for v in json.loads(content)):
                        raise error("模型输出被截断")       # 复读停不下来：输出被截断或超时
                    return super().json_chat(system, content)
            events = []
            with self.subTest(error=error.__name__), tempfile.TemporaryDirectory() as tmp:
                result = translate([cue(i, f"line {i}") for i in range(40)], Looping(), 8,
                                   Path(tmp) / "c.json", report=events.append)
                self.assertEqual(result[5].text, "line 5")
                self.assertEqual([e["position"] for e in events if e["type"] == "kept"], [5])

    def test_long_runs_of_one_character_are_shortened_before_sending(self):
        sent = []

        class Recording(ScriptedClient):
            def json_chat(self, system, content):
                sent.extend(v["text"] for v in json.loads(content))
                return super().json_chat(system, content)
        with tempfile.TemporaryDirectory() as tmp:
            result = translate([cue(0, "มัย" + "ย" * 52 + "ไป"), cue(1, "ok!!!")], Recording(), 20, Path(tmp) / "c.json")
        self.assertEqual(sent, ["มัยยยยไป", "ok!!!"])
        self.assertEqual(result[0].start, 0)

    def test_too_many_untranslatable_cues_still_fail_the_file(self):
        class Broken(ScriptedClient):
            def json_chat(self, system, content):
                return "乱码"
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(InvalidResponse, "清理字幕"):
                translate([cue(i) for i in range(40)], Broken(), 8, Path(tmp) / "c.json")

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
        self.assertEqual(row.text(now=142), "翻译 40/400")
        self.assertIn("第 3/20 批", row.detail(now=142))
        self.assertIn("请求中 42s", row.detail(now=142))
        self.assertEqual(row.color(), "#5264e8")
        self.assertAlmostEqual(row.fraction(), 0.1)

    def test_retry_turns_amber_and_explains_why(self):
        row = RowProgress()
        row.progress("翻译", 0, 10)
        row.apply({"type": "batch", "batch": 1, "batches": 1}, now=0)
        row.apply({"type": "request", "attempt": 1}, now=0)
        row.apply({"type": "retry", "attempt": 1, "reason": "请求超时", "delay": 2}, now=60)
        self.assertEqual(row.text(now=61), "翻译 0/10 · 第 1 次重试：请求超时（2s 后）")
        self.assertEqual(row.color(), "#b7791f")
        row.apply({"type": "request", "attempt": 2}, now=62)
        self.assertEqual(row.text(now=67), "翻译 0/10")
        self.assertIn("请求中 5s（第 2 次尝试）", row.detail(now=67))
        self.assertEqual(row.color(), "#b7791f")

    def test_garbled_and_split_notes(self):
        row = RowProgress()
        row.apply({"type": "invalid", "message": "译文条目数量不匹配"}, now=0)
        self.assertEqual(row.text(now=0), "译文格式错误，重试中：译文条目数量不匹配")
        row.apply({"type": "split", "sizes": [10, 10]}, now=0)
        self.assertEqual(row.text(now=0), "输出过长，拆分为 10+10")

    def test_retry_note_survives_the_request_that_follows_it(self):
        row = RowProgress()
        row.start()
        row.apply({"type": "batch", "batch": 1, "batches": 3}, now=0)
        row.apply({"type": "request", "attempt": 1}, now=0)
        row.apply({"type": "invalid", "message": "译文条目数量不匹配"}, now=1)
        row.apply({"type": "request", "attempt": 1}, now=1)
        self.assertEqual(row.text(now=4), "译文格式错误，重试中：译文条目数量不匹配")
        self.assertIn("请求中 3s", row.detail(now=4))
        self.assertEqual(row.color(), "#b7791f")
        row.progress("翻译", 20, 60)          # 这批成功：说明与琥珀色一起撤掉
        self.assertNotIn("译文格式错误", row.text(now=5))
        self.assertEqual(row.color(), "#5264e8")

    def test_split_note_lasts_until_a_half_finishes(self):
        row = RowProgress()
        row.start()
        row.apply({"type": "split", "sizes": [2, 2]}, now=0)
        row.apply({"type": "request", "attempt": 1}, now=0)
        self.assertIn("输出过长，拆分为 2+2", row.text(now=1))
        row.progress("翻译", 2, 4)
        self.assertNotIn("拆分", row.text(now=2))

    def test_timeout_split_is_explained(self):
        row = RowProgress()
        row.apply({"type": "split", "sizes": [10, 10], "reason": "timeout"}, now=0)
        self.assertEqual(row.text(now=0), "响应超时，拆分为 10+10")

    def test_untranslated_cues_are_counted_on_the_row(self):
        row = RowProgress()
        row.start()
        row.progress("翻译", 10, 40)
        row.apply({"type": "kept", "position": 3, "message": "译文条目数量不匹配"}, now=0)
        row.apply({"type": "kept", "position": 17, "message": "存在空译文"}, now=0)
        self.assertIn("2 条保留原文", row.text(now=0))
        self.assertIn(("保留原文", "第 4、18 条", "warn"), row.detail_rows(now=0))
        row.progress("翻译", 40, 40)
        row.finish("done")
        self.assertEqual(row.text(), "40 条 · 2 条保留原文")

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

    def test_events_drive_row_bar_and_overall_bar(self):
        from subtitleflow.progress_view import ProgressDelegate
        w = self.window
        self.assertIsInstance(w.table.itemDelegateForColumn(2), ProgressDelegate)
        w.job_event(("file", 0, "已完成", ""))
        w.job_event(("file", 1, "翻译中", ""))
        w.job_event(("progress", 1, "翻译", 20, 40))
        w.job_event(("activity", 1, {"type": "batch", "batch": 2, "batches": 2, "stage": "翻译"}))
        status = w.status.text()
        w.job_event(("activity", 1, {"type": "retry", "attempt": 1, "reason": "HTTP 503", "delay": 1, "stage": "翻译"}))
        item = w.table.item(2, 2)                 # 第二个文件在表格第 2 行；第 1、3 行是各自的详情面板
        self.assertIn("HTTP 503", item.text())
        self.assertIn("第 2/2 批", w.row_progress[1].detail())
        self.assertEqual(item.data(ProgressDelegate.ROLE), (0.5, "#b7791f"))
        self.assertEqual(w.progress.value(), 750)
        self.assertEqual(w.progress_label.text(), "1/2 个文件 · 75%")
        self.assertEqual(w.status.text(), status)  # 重试只显示在该文件上，不写到底部状态栏

    def test_detail_panel_opens_below_its_file_without_changing_it(self):
        w = self.window
        w.resize(1080, 760)
        base = w.table.rowHeight(2)
        self.assertTrue(w.table.isRowHidden(3))
        w.toggle_detail(1)                      # 点 b.srt 前面的 ▸
        self.assertFalse(w.table.isRowHidden(3))
        self.assertTrue(w.table.isRowHidden(1))           # a.srt 的面板仍收起
        self.assertEqual(w.table.rowHeight(2), base)      # 文件行本身高度不变
        self.assertEqual(w.table.item(2, 0).text(), "b.srt")
        w.job_event(("file", 1, "翻译中", ""))
        w.job_event(("progress", 1, "翻译", 20, 40))
        w.job_event(("activity", 1, {"type": "batch", "batch": 2, "batches": 2, "first": 20, "last": 39,
                                      "at": 61000, "preview": "hello", "stage": "翻译"}))
        text = w.detail_panel(1).text()
        self.assertIn("第 21–40 条", text)
        self.assertIn("00:01:01", text)
        self.assertIn("hello", text)
        self.assertGreater(w.table.rowHeight(3), 40)
        self.assertFalse(w.table.grab().isNull())
        w.toggle_detail(1)                       # 收起
        self.assertTrue(w.table.isRowHidden(3))

    def test_request_timeout_setting_is_used_and_saved(self):
        w = self.window
        w.base.setText("https://127.0.0.1:8081/api")
        self.assertIsNone(w.api_config().timeout)
        self.assertEqual(w.api_config().request_timeout(), 300)
        w.timeout.setValue(120)
        self.assertEqual(w.api_config().request_timeout(), 120)
        w.save_settings()
        w.timeout.setValue(0)
        w.load_settings()
        self.assertEqual(w.timeout.value(), 120)

    def test_arrow_click_maps_table_row_to_file(self):
        w = self.window
        w.name_delegate.toggled.emit(2)          # 表格第 2 行 = 第二个文件
        self.assertFalse(w.table.isRowHidden(3))

    def test_row_is_painted_without_errors(self):
        w = self.window
        w.resize(1080, 760)
        w.job_event(("progress", 0, "翻译", 1, 4))
        image = w.table.grab()
        self.assertFalse(image.isNull())


if __name__ == "__main__":
    unittest.main()
