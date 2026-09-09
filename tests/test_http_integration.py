from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
from subtitleflow.api import APIConfig, Client
from subtitleflow.srt import Cue
from subtitleflow.translate import translate, ai_boundaries


class Handler(BaseHTTPRequestHandler):
    requests = []
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).requests.append((self.path, body))
        items = json.loads(body["messages"][1]["content"])
        answer = ([x["id"] for x in items] if "sentence ends" in body["messages"][0]["content"]
                  else [{"id": x["id"], "text": "真实 HTTP 模拟译文。"} for x in items])
        data = json.dumps({"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(answer)}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class HTTPIntegration(unittest.TestCase):
    def test_real_http_translation_and_sentence_boundaries(self):
        Handler.requests = []
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = Client(APIConfig(f"http://127.0.0.1:{server.server_port}/v1", "test-key", "mock-model"), threading.Event())
            cues = [Cue(i + 1, i * 1000, (i + 1) * 1000, "Hello.") for i in range(4)]
            with tempfile.TemporaryDirectory() as tmp:
                translated = translate(cues, client, 2, Path(tmp) / "cache.json")
            self.assertEqual(translated[0].text, "真实 HTTP 模拟译文。")
            self.assertEqual(ai_boundaries(cues, client, 2), set(range(4)))
            self.assertTrue(all(path == "/v1/chat/completions" for path, _ in Handler.requests))
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
