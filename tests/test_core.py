import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from subtitleflow.srt import Cue, parse, read, render
from subtitleflow.merge import merge, MergeOptions, sentence_end
from subtitleflow.api import APIConfig, Cancelled, InvalidResponse, TooLarge
from subtitleflow.translate import translate, ai_boundaries
from subtitleflow.jobs import Job, JobOptions


def cue(start, end, text, id=1):
    return Cue(id, int(start * 1000), int(end * 1000), text)


class FakeClient:
    def __init__(self, config=None, cancel=None):
        self.config = config or APIConfig("https://example.test/v1", "secret", "test")
        self.cancel = cancel or threading.Event()
        self.calls = []
        self.maximum = 100
        self.bad = False

    def check(self):
        if self.cancel.is_set():
            raise Cancelled()

    def json_chat(self, system, content):
        self.check()
        values = json.loads(content)
        self.calls.append(values)
        if len(values) > self.maximum:
            raise TooLarge()
        if self.bad:
            return [{"id": values[0]["id"], "text": ""}]
        return [{"id": x["id"], "text": "中文译文"} for x in reversed(values)]


class SRTTests(unittest.TestCase):
    def test_roundtrip_and_multiline(self):
        cues = [cue(0, 2.123, "Hello\nworld.", 7), cue(2, 3, "Hi", 19)]
        self.assertEqual(parse("\ufeff" + render(cues).replace("\n", "\r\n")), cues)

    def test_invalid(self):
        for text in ("", "1\nBAD\nText", "1\n00:00:02,000 --> 00:00:01,000\nText"):
            with self.assertRaises(ValueError):
                parse(text)

    def test_encodings(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "字幕.srt"
            for encoding in ("utf-8-sig", "gb18030", "utf-16"):
                path.write_bytes(render([cue(0, 1, "你好")]).encode(encoding))
                self.assertEqual(read(path)[0].text, "你好")

    def test_reverse_time(self):
        with self.assertRaises(ValueError):
            parse(render([cue(2, 4, "A"), cue(1, 3, "B")]))


class MergeTests(unittest.TestCase):
    def test_fragments(self):
        result = merge([cue(0, 2, "这"), cue(2, 3, "是"), cue(3, 5, "一句。")], MergeOptions())
        self.assertEqual(result, [cue(0, 5, "这是一句。")])

    def test_exact_target(self):
        result = merge([cue(0, 5, "Hello"), cue(5, 10, "world."), cue(10, 12, "Next.")], MergeOptions())
        self.assertEqual(len(result), 2)

    def test_complete_long_sentence(self):
        result = merge([cue(0, 6, "This"), cue(6, 12, "is a long"), cue(12, 18, "sentence.")], MergeOptions())
        self.assertEqual(result[0].end, 12000)
        self.assertEqual(len(result), 2)

    def test_silence_threshold(self):
        for gap, count in ((2.999, 1), (3, 2), (4, 2)):
            self.assertEqual(len(merge([cue(1, 3, "A"), cue(3 + gap, 5 + gap, "B.")], MergeOptions())), count)

    def test_fallback_cap(self):
        result = merge([cue(0, 6, "a"), cue(6, 12, "b"), cue(12, 18, "c")], MergeOptions())
        self.assertEqual([(c.start, c.end) for c in result], [(0, 12000), (12000, 18000)])

    def test_single_long_and_overlap(self):
        self.assertEqual(merge([cue(0, 20, "long"), cue(1, 2, "nested.")], MergeOptions())[0].end, 20000)
        self.assertEqual(merge([cue(0, 20, "long")], MergeOptions())[0].end, 20000)

    def test_abbreviation_and_quote(self):
        self.assertFalse(sentence_end("Dr."))
        self.assertFalse(sentence_end("Price is 3.14"))
        self.assertTrue(sentence_end('“结束。”'))
        self.assertTrue(sentence_end("Done."))

    def test_ai_boundary_and_silence(self):
        values = [cue(0, 10, "a"), cue(10, 18, "b"), cue(21, 23, "c")]
        result = merge(values, MergeOptions(), {1})
        self.assertEqual([c.end for c in result], [10000, 18000, 23000])


class TranslationTests(unittest.TestCase):
    def test_batches_order_timestamps_and_resume(self):
        cues = [cue(i, i + 1, "x", i + 4) for i in range(45)]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cache.json"
            client = FakeClient()
            result = translate(cues, client, 20, path)
            self.assertEqual([len(c) for c in client.calls], [20, 20, 5])
            self.assertEqual([(c.id, c.start, c.end) for c in cues], [(c.id, c.start, c.end) for c in result])
            self.assertTrue(all(c.text == "中文译文" for c in result))
            client.calls.clear()
            translate(cues, client, 20, path)
            self.assertEqual(client.calls, [])
            client.config = APIConfig("https://example.test/v1", "secret", "different")
            translate(cues, client, 20, path)
            self.assertEqual(len(client.calls), 3)

    def test_context_splitting(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = FakeClient()
            client.maximum = 2
            result = translate([cue(i, i + 1, "x") for i in range(5)], client, 20, Path(tmp) / "cache.json")
            self.assertEqual(len(result), 5)
            self.assertGreater(len(client.calls), 1)

    def test_missing_translation_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = FakeClient()
            client.bad = True
            with self.assertRaises(InvalidResponse):
                translate([cue(0, 1, "x")], client, 20, Path(tmp) / "cache.json")
            self.assertFalse((Path(tmp) / "cache.json").exists())

    def test_cancel_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = FakeClient()
            path = Path(tmp) / "cache.json"
            cues = [cue(i, i + 1, "x") for i in range(5)]
            def progress(done, total):
                if done == 2:
                    client.cancel.set()
            with self.assertRaises(Cancelled):
                translate(cues, client, 2, path, progress)
            client.cancel.clear()
            client.calls.clear()
            translate(cues, client, 2, path)
            self.assertEqual([len(c) for c in client.calls], [2, 1])

    def test_no_watermark(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = render(translate([cue(0, 1, "Hello")], FakeClient(), 20, Path(tmp) / "cache.json"))
            self.assertNotIn("SubtitleFlow", result)
            self.assertNotIn("license", result.lower())


class JobTests(unittest.TestCase):
    def test_ten_files_same_names_and_failure_isolation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = []
            for i in range(10):
                parent = root / str(i)
                parent.mkdir()
                path = parent / "字幕.srt"
                path.write_text(render([cue(0, 1, "Hello.")]), encoding="utf-8")
                paths.append(path)
            with patch("subtitleflow.jobs.Client", FakeClient):
                output = Job(paths, root / "out", JobOptions(), APIConfig("https://example.test/v1", "secret", "test"),
                             threading.Event()).run()
                for folder in ("原始的srt", "合并后的srt", "翻译后的srt"):
                    self.assertEqual(len(list((output / folder).glob("*.srt"))), 10)
                self.assertEqual((output / "原始的srt/字幕.srt").read_bytes(), paths[0].read_bytes())
                self.assertNotIn("secret", (output / "report.json").read_text())
                paths[0].write_text("broken", encoding="utf-8")
                output2 = Job(paths, root / "out", JobOptions(), APIConfig("https://example.test/v1", "secret", "test"),
                              threading.Event()).run()
                report = json.loads((output2 / "report.json").read_text(encoding="utf-8"))
                self.assertEqual(report["files"][0]["status"], "failed")
                self.assertEqual(sum(f["status"] == "done" for f in report["files"]), 9)


    def test_restore_legacy_original_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "a.srt"
            source.write_text(render([cue(0, 1, "A.")]), encoding="utf-8")
            options, api = JobOptions(mode="merge"), APIConfig("", "")
            output = Job([source], base / "out", options, api, threading.Event()).run()
            (output / "原始的srt").rename(output / "originals")
            (output / "合并后的srt").rename(output / "merged")
            Job([], base / "out", options, api, threading.Event(), resume=output).run()
            self.assertTrue((output / "原始的srt/a.srt").exists())
            self.assertTrue((output / "合并后的srt/a.srt").exists())
            self.assertFalse((output / "originals").exists())

    def test_numbering_only_reaches_the_translated_srt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "a.srt"
            path.write_text(render([cue(0, 1, "One.", 7), cue(1, 2, "Two.", 7)]), encoding="utf-8")
            options = JobOptions(mode="translate", number_translated=True, export_fcpxml=True)
            with patch("subtitleflow.jobs.Client", FakeClient):
                output = Job([path], root / "out", options, APIConfig("https://example.test/v1", "secret", "test"),
                             threading.Event()).run()
            translated = read(output / "翻译后的srt/a.srt")
            self.assertEqual([(c.id, c.text) for c in translated], [(1, "1 中文译文"), (2, "2 中文译文")])
            self.assertNotIn("1 中文译文", (output / "转换后的fcpxml/a.fcpxml").read_text(encoding="utf-8"))

    def test_merge_without_api(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.srt"
            path.write_text(render([cue(0, 1, "A.")]), encoding="utf-8")
            output = Job([path], Path(tmp), JobOptions(mode="merge"), APIConfig("", "", ""), threading.Event()).run()
            self.assertTrue((output / "合并后的srt/a.srt").exists())
            self.assertFalse((output / "翻译后的srt").exists())


if __name__ == "__main__":
    unittest.main()
