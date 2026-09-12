import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

from transcriber import API_VERSION
from transcriber.fakes import FakeEngine, FakeMedia
from transcriber.server import create_server
from transcriber.service import TranscriptionService

TOKEN = "test-token"


def request(base, method, path, body=None, token=TOKEN):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.server = create_server(TranscriptionService(FakeEngine(), FakeMedia()), TOKEN)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = "http://127.0.0.1:%d" % self.server.server_address[1]

    def media(self, **spec):
        path = Path(self.tmp.name) / f"media-{len(list(Path(self.tmp.name).iterdir()))}.wav"
        path.write_text(json.dumps(spec), encoding="utf-8")
        return str(path)

    def wait(self, job_id):
        deadline = time.time() + 10
        while time.time() < deadline:
            _, status = request(self.base, "GET", f"/v1/jobs/{job_id}")
            if status["status"] in ("done", "failed", "cancelled"):
                return status
            time.sleep(0.02)
        self.fail("job did not finish")

    def test_requests_without_valid_token_are_rejected(self):
        self.assertEqual(request(self.base, "GET", "/v1/health", token=None)[0], 401)
        self.assertEqual(request(self.base, "GET", "/v1/health", token="wrong")[0], 401)
        status, body = request(self.base, "GET", "/v1/health")
        self.assertEqual((status, body["api_version"]), (200, API_VERSION))

    def test_transcribes_source_file_into_whole_file_cues(self):
        source = self.media(duration=700, silences=[[299, 301]])
        status, body = request(self.base, "POST", "/v1/jobs", {"path": source, "segment_seconds": 300})
        self.assertEqual(status, 201)
        final = self.wait(body["id"])
        self.assertEqual((final["status"], final["segment"], final["segments"], final["duration"]), ("done", 2, 2, 700))
        status, result = request(self.base, "GET", f"/v1/jobs/{body['id']}/result")
        self.assertEqual(status, 200)
        self.assertEqual(result["cues"], [
            {"start": 0, "end": 300000, "text": "第1段"},
            {"start": 300000, "end": 700000, "text": "第2段"},
        ])

    def test_negative_content_length_is_rejected_without_hanging(self):
        import http.client
        host, port = self.server.server_address
        conn = http.client.HTTPConnection(host, port, timeout=5)
        conn.putrequest("POST", "/v1/jobs")
        conn.putheader("Content-Length", "-1")
        conn.endheaders()
        self.assertEqual(conn.getresponse().status, 400)
        conn.close()

    def test_engines_endpoint_reports_what_this_service_can_actually_run(self):
        status, body = request(self.base, "GET", "/v1/engines")
        self.assertEqual(status, 200)
        self.assertEqual([e["name"] for e in body["engines"]], ["fake"])
        engine = body["engines"][0]
        self.assertEqual((engine["default_model"], engine["diarization"]), ("fake", False))
        self.assertIn("auto", engine["languages"])

    def test_job_records_the_engine_options_it_ran_with(self):
        source = self.media(duration=60)
        _, body = request(self.base, "POST", "/v1/jobs",
                          {"path": source, "segment_seconds": 300, "engine": "fake", "language": "zh"})
        self.assertEqual((body["engine"], body["model"], body["language"]), ("fake", "fake", "zh"))
        # 省略时回落到引擎默认值
        _, body = request(self.base, "POST", "/v1/jobs", {"path": source, "segment_seconds": 300})
        self.assertEqual((body["engine"], body["model"], body["language"]), ("fake", "fake", "auto"))

    def test_unavailable_engine_options_are_rejected_instead_of_silently_ignored(self):
        source = self.media(duration=60)
        for extra, expected in (({"engine": "whisper"}, "未提供引擎"),
                                ({"model": "large-v3"}, "没有模型"),
                                ({"language": "de"}, "不支持语言")):
            status, body = request(self.base, "POST", "/v1/jobs",
                                   {"path": source, "segment_seconds": 300, **extra})
            self.assertEqual(status, 400, extra)
            self.assertIn(expected, body["error"])

    def test_missing_source_file_is_rejected(self):
        status, body = request(self.base, "POST", "/v1/jobs", {"path": str(Path(self.tmp.name) / "none.wav"), "segment_seconds": 300})
        self.assertEqual(status, 400)
        self.assertIn("源文件不存在", body["error"])

    def test_engine_failure_fails_job_and_service_keeps_working(self):
        source = self.media(duration=60, fail="显存不足")
        _, body = request(self.base, "POST", "/v1/jobs", {"path": source, "segment_seconds": 300})
        final = self.wait(body["id"])
        self.assertEqual(final["status"], "failed")
        self.assertIn("显存不足", final["error"])
        _, body = request(self.base, "POST", "/v1/jobs", {"path": self.media(duration=60), "segment_seconds": 300})
        self.assertEqual(self.wait(body["id"])["status"], "done")

    def test_cancel_stops_running_segment_without_waiting_for_it(self):
        _, body = request(self.base, "POST", "/v1/jobs", {"path": self.media(duration=60, delay=30), "segment_seconds": 300})
        while request(self.base, "GET", f"/v1/jobs/{body['id']}")[1]["status"] != "running":
            time.sleep(0.01)
        started = time.time()
        self.assertEqual(request(self.base, "POST", f"/v1/jobs/{body['id']}/cancel", {})[0], 200)
        self.assertEqual(self.wait(body["id"])["status"], "cancelled")
        self.assertLess(time.time() - started, 2)
        self.assertEqual(request(self.base, "GET", f"/v1/jobs/{body['id']}/result")[0], 409)
        _, body = request(self.base, "POST", "/v1/jobs", {"path": self.media(duration=60), "segment_seconds": 300})
        self.assertEqual(self.wait(body["id"])["status"], "done")


class IdleUnloadTests(unittest.TestCase):
    def test_model_is_unloaded_after_idle_period_and_reloaded_on_demand(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        source = Path(tmp.name) / "a.wav"
        source.write_text(json.dumps({"duration": 60}), encoding="utf-8")
        engine = FakeEngine()
        service = TranscriptionService(engine, FakeMedia(), idle_unload_seconds=0.3)
        job = service.submit(str(source), 300)
        while job.status != "done":
            time.sleep(0.01)
        self.assertTrue(engine.loaded)
        time.sleep(1.5)
        self.assertFalse(engine.loaded)
        again = service.submit(str(source), 300)
        while again.status != "done":
            time.sleep(0.01)
        self.assertEqual(len(again.cues), 1)


class ProcessTests(unittest.TestCase):
    def test_building_the_moss_service_does_not_import_torch(self):
        root = Path(__file__).resolve().parent.parent
        code = ("import sys; from transcriber.__main__ import build_service, parser; "
                "build_service(parser().parse_args([])); print('torch' in sys.modules)")
        output = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, check=True)
        self.assertEqual(output.stdout.strip(), "False")

    def test_service_process_announces_port_token_and_version(self):
        root = Path(__file__).resolve().parent.parent
        proc = subprocess.Popen([sys.executable, "-m", "transcriber", "--engine", "fake"], cwd=root,
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        self.addCleanup(proc.wait, 10)
        self.addCleanup(proc.kill)
        hello = json.loads(proc.stdout.readline())
        self.assertEqual(hello["api_version"], API_VERSION)
        status, _ = request(f"http://127.0.0.1:{hello['port']}", "GET", "/v1/health", token=hello["token"])
        self.assertEqual(status, 200)


if __name__ == "__main__":
    unittest.main()
