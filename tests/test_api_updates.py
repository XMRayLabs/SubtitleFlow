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

    def test_timeout_retry_limit(self):
        client = Client(APIConfig("https://host", "", "m"), threading.Event())
        with patch("urllib.request.OpenerDirector.open", side_effect=TimeoutError()) as mocked, patch.object(client.cancel, "wait", return_value=False):
            with self.assertRaises(APIError):
                client.chat("s", "u")
            self.assertEqual(mocked.call_count, 4)


class UpdateTests(unittest.TestCase):
    def test_disabled(self):
        with patch("urllib.request.urlopen") as mocked:
            self.assertIsNone(updates.check(""))
            mocked.assert_not_called()

    def test_url_and_versions(self):
        self.assertTrue(updates.allowed_url("https://github.com/a/b/releases/download/v1/a.exe", "a/b"))
        self.assertFalse(updates.allowed_url("https://evil.test/a/b/releases/download/v1/a.exe", "a/b"))
        self.assertGreater(updates.version("1.10.0"), updates.version("1.9.0"))

    def test_checksum(self):
        with tempfile.TemporaryDirectory() as tmp:
            release = updates.Release("1.0.0", "https://github.com/a/b/releases/download/v1/a.exe", "0" * 64, "a.exe")
            with patch("urllib.request.urlopen", return_value=io.BytesIO(b"broken")):
                with self.assertRaises(ValueError):
                    updates.download(release, Path(tmp), threading.Event())
            self.assertFalse((Path(tmp) / "a.exe").exists())
            self.assertEqual(list(Path(tmp).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
