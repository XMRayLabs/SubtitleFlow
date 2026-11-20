import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
from unittest.mock import patch
from subtitleflow.api import APIConfig, Client, APIError, TooLarge, Cancelled
from subtitleflow import updates


class APItests(unittest.TestCase):
    def test_endpoint(self):
        self.assertEqual(APIConfig("https://host/v1/", "", "m").endpoint(), "https://host/v1/chat/completions")
        self.assertEqual(APIConfig("http://localhost:8000/v1/chat/completions", "", "m").endpoint(), "http://localhost:8000/v1/chat/completions")
        with self.assertRaises(ValueError):
            APIConfig("https://user:secret@host/v1", "", "m").endpoint()

    def test_retry_and_auth(self):
        client = Client(APIConfig("https://host/v1", "TOP_SECRET", "m"), threading.Event())
        error = urllib.error.HTTPError("https://host", 429, "limit", {}, io.BytesIO(b"TOP_SECRET"))
        response = io.BytesIO(json.dumps({"choices": [{"message": {"content": "OK"}}]}).encode())
        with patch("urllib.request.OpenerDirector.open", side_effect=[error, response]) as mocked, patch.object(client.cancel, "wait", return_value=False):
            self.assertEqual(client.chat("s", "u"), "OK")
            self.assertEqual(mocked.call_count, 2)
        error = urllib.error.HTTPError("https://host", 401, "auth", {}, io.BytesIO(b"TOP_SECRET"))
        with patch("urllib.request.OpenerDirector.open", side_effect=error):
            with self.assertRaises(APIError) as caught:
                client.chat("s", "u")
            self.assertNotIn("TOP_SECRET", str(caught.exception))

    def test_length(self):
        response = io.BytesIO(b'{"choices":[{"finish_reason":"length","message":{"content":"partial"}}]}')
        with patch("urllib.request.OpenerDirector.open", return_value=response):
            with self.assertRaises(TooLarge):
                Client(APIConfig("https://host", "", "m"), threading.Event()).chat("s", "u")

    def test_timeout_is_retried_once_then_reported_as_timeout(self):
        # 本地服务断开后往往还在算旧请求，连发 4 次只会越排越久；超时只再试一次，交给调用方拆小批
        from subtitleflow.api import RequestTimeout
        client = Client(APIConfig("https://host", "", "m"), threading.Event())
        for error in (TimeoutError(), urllib.error.URLError(TimeoutError("timed out"))):
            with self.subTest(error=error), \
                    patch("urllib.request.OpenerDirector.open", side_effect=error) as mocked, \
                    patch.object(client.cancel, "wait", return_value=False):
                with self.assertRaises(RequestTimeout) as caught:
                    client.chat("s", "u")
                self.assertEqual(mocked.call_count, 2)
                self.assertIn("60 秒", str(caught.exception))

    def test_reply_length_is_capped_by_the_request_size(self):
        # 模型复读停不下来时，有上限才能很快以“输出被截断”收场，而不是一直生成到超时
        client = Client(APIConfig("https://host/v1", "", "m"), threading.Event())
        bodies = []

        def capture(request, timeout=None):
            bodies.append(json.loads(request.data))
            return io.BytesIO(json.dumps({"choices": [{"message": {"content": "[]"}}]}).encode())
        with patch("urllib.request.OpenerDirector.open", side_effect=capture):
            client.json_chat("s", "x" * 100)
            client.json_chat("s", "x" * 1000)
        self.assertGreater(bodies[1]["max_tokens"], bodies[0]["max_tokens"])
        self.assertLessEqual(bodies[0]["max_tokens"], 2048 + 4 * 100)

    def test_dropped_connection_still_gets_four_attempts(self):
        client = Client(APIConfig("https://host", "", "m"), threading.Event())
        with patch("urllib.request.OpenerDirector.open", side_effect=ConnectionResetError()) as mocked, \
                patch.object(client.cancel, "wait", return_value=False):
            with self.assertRaises(APIError):
                client.chat("s", "u")
            self.assertEqual(mocked.call_count, 4)

    def test_timeout_defaults_to_longer_for_local_models(self):
        self.assertEqual(APIConfig("https://127.0.0.1:8081/api", "", "m").request_timeout(), 300)
        self.assertEqual(APIConfig("http://localhost:11434", "", "m").request_timeout(), 300)
        self.assertEqual(APIConfig("https://api.example.com/v1", "", "m").request_timeout(), 60)
        self.assertEqual(APIConfig("https://127.0.0.1:8081/api", "", "m", timeout=90).request_timeout(), 90)


class UpdateTests(unittest.TestCase):
    def test_disabled(self):
        with patch("subtitleflow.updates.open_update") as mocked:
            self.assertIsNone(updates.check(""))
            mocked.assert_not_called()

    def test_url_and_versions(self):
        self.assertTrue(updates.allowed_url("https://github.com/a/b/releases/download/v1/a.exe", "a/b"))
        self.assertFalse(updates.allowed_url("https://evil.test/a/b/releases/download/v1/a.exe", "a/b"))
        self.assertGreater(updates.version("1.10.0"), updates.version("1.9.0"))

    def test_checksum(self):
        with tempfile.TemporaryDirectory() as tmp:
            release = updates.Release("1.0.0", "https://github.com/a/b/releases/download/v1/a.exe", "0" * 64, "a.exe")
            with patch("subtitleflow.updates.open_update", return_value=io.BytesIO(b"broken")):
                with self.assertRaises(ValueError):
                    updates.download(release, Path(tmp), threading.Event())
            self.assertFalse((Path(tmp) / "a.exe").exists())
            self.assertEqual(list(Path(tmp).iterdir()), [])


class JsonExtractionTests(unittest.TestCase):
    def reply(self, text):
        from subtitleflow.api import Client
        client = Client(APIConfig("https://example.test/v1", "k", "m"), threading.Event())
        client.chat = lambda system, content, **options: text
        return client.json_chat("", "")

    def test_accepts_replies_that_reasoning_models_actually_produce(self):
        expected = [{"id": 0, "text": "你好"}]
        for text in (
            '[{"id": 0, "text": "你好"}]',
            '```json\n[{"id": 0, "text": "你好"}]\n```',
            '<think>先看 [1] 号</think>\n[{"id": 0, "text": "你好"}]',
            '好的，结果如下：\n```json\n[{"id": 0, "text": "你好"}]\n```\n希望有帮助',
            '结果：[{"id": 0, "text": "你好"}] 完毕',
        ):
            with self.subTest(text=text):
                self.assertEqual(self.reply(text), expected)

    def test_bracketed_numbers_in_reasoning_are_not_taken_as_the_answer(self):
        expected = [{"id": 0, "text": "你好"}]
        for text in (
            '先核对 [1] 和 [2] 两条。\n</think>\n[{"id": 0, "text": "你好"}]',   # 只有结尾标签
            'Note [1]: 保留人名。\n[{"id": 0, "text": "你好"}]',
            '[{"id": 0, "text": "你好"}]\n参考 [1]',
        ):
            with self.subTest(text=text):
                self.assertEqual(self.reply(text), expected)

    def test_sentence_end_ids_survive_a_cited_number(self):
        self.assertEqual(self.reply("第 [2] 句存疑。\n[3, 5, 7]"), [3, 5, 7])

    def test_text_without_json_is_still_rejected(self):
        from subtitleflow.api import InvalidResponse
        with self.assertRaises(InvalidResponse):
            self.reply("抱歉，我无法完成这个请求")


class CancelDuringBodyTests(unittest.TestCase):
    def test_cancel_while_reading_body_is_cancelled_not_a_failure(self):
        from subtitleflow.safety import bounded_response
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(Cancelled):
            bounded_response(io.BytesIO(b"{}"), cancel=cancel)


if __name__ == "__main__":
    unittest.main()
