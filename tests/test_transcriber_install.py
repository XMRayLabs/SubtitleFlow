import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.request
import zipfile

from nacl.signing import SigningKey

from subtitleflow import transcriber_install as install
from subtitleflow import update_trust

REVISION = "e8681d68e7042738ffca8ac8212bc8fcb1131ab8"


def sha(data):
    return hashlib.sha256(data).hexdigest()


class FileServer:
    """Serves bytes by path with Range support; records requests; paths in `fail` return 404."""

    def __init__(self, files):
        self.files, self.requests, self.fail = files, [], set()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                owner.requests.append((self.path, self.headers.get("Range")))
                data = owner.files.get(self.path)
                if data is None or self.path in owner.fail:
                    self.send_response(404)
                    self.end_headers()
                    return
                start = 0
                if self.headers.get("Range"):
                    start = int(self.headers["Range"].split("=")[1].rstrip("-"))
                    self.send_response(206)
                    self.send_header("Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}")
                else:
                    self.send_response(200)
                self.send_header("Content-Length", str(len(data) - start))
                self.end_headers()
                self.wfile.write(data[start:])

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def plain_opener(url, timeout):
    return urllib.request.urlopen(url, timeout=timeout)


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "install"
        bundle = io.BytesIO()
        with zipfile.ZipFile(bundle, "w") as zf:
            zf.writestr("SubtitleFlow-Transcriber/SubtitleFlow-Transcriber.exe", b"MZ fake service")
            zf.writestr("SubtitleFlow-Transcriber/_internal/lib.dll", b"x" * 5000)
        archive = bundle.getvalue()
        self.parts = {"s.zip.001": archive[:3000], "s.zip.002": archive[3000:]}
        self.model = {"config.json": b'{"a": 1}', "sub/modeling.py": b"print('model')"}
        self.release = install.parse_manifest({
            "kind": "subtitleflow-transcriber", "version": "1.0.0", "api_version": 1, "platform": "windows-x86_64",
            "parts": [{"name": n, "size": len(d), "sha256": sha(d)} for n, d in self.parts.items()],
            "archive_sha256": sha(archive), "archive_size": len(archive),
            "entry": "SubtitleFlow-Transcriber/SubtitleFlow-Transcriber.exe",
            "model": {"repo": "OpenMOSS-Team/MOSS-Transcribe-Diarize", "revision": REVISION,
                      "files": {n: {"size": len(d), "sha256": sha(d)} for n, d in self.model.items()}},
        })
        files = {f"/rel/{n}": d for n, d in self.parts.items()}
        files.update({f"/hf/OpenMOSS-Team/MOSS-Transcribe-Diarize/resolve/{REVISION}/{n}": d for n, d in self.model.items()})
        files.update({f"/mirror/OpenMOSS-Team/MOSS-Transcribe-Diarize/resolve/{REVISION}/{n}": d for n, d in self.model.items()})
        self.server = FileServer(files)
        self.addCleanup(self.server.close)

    def installer(self, **kwargs):
        return install.Installer(self.release, self.root, threading.Event(), parts_base=self.server.base + "/rel",
                                 model_endpoints=[self.server.base + "/hf", self.server.base + "/mirror"],
                                 opener=plain_opener, **kwargs)

    def test_install_downloads_verifies_extracts_and_records_service(self):
        service = self.installer().run()
        self.assertEqual(service.exe.read_bytes(), b"MZ fake service")
        self.assertEqual((service.model_dir / "sub" / "modeling.py").read_bytes(), b"print('model')")
        again = install.installed_service(self.root)
        self.assertEqual((again.version, again.api_version, again.exe), ("1.0.0", 1, service.exe))
        self.assertEqual(again.command(), [str(service.exe), "--model", str(service.model_dir)])

    def test_interrupted_install_resumes_where_it_stopped(self):
        cancel = threading.Event()

        def stop_after_first_chunk(done, total, label):
            if done >= 1000:
                cancel.set()

        first = install.Installer(self.release, self.root, cancel, stop_after_first_chunk, parts_base=self.server.base + "/rel",
                                  model_endpoints=[self.server.base + "/hf"], opener=plain_opener, chunk_size=1000)
        with self.assertRaises(install.Cancelled):
            first.run()
        self.assertIsNone(install.installed_service(self.root))
        self.installer().run()
        resumed = [r for p, r in self.server.requests if p == "/rel/s.zip.001" and r]
        self.assertEqual(resumed, ["bytes=1000-"])
        self.assertIsNotNone(install.installed_service(self.root))

    def test_model_download_falls_back_to_mirror(self):
        self.server.fail = {p for p in self.server.files if p.startswith("/hf/")}
        service = self.installer().run()
        self.assertEqual((service.model_dir / "config.json").read_bytes(), b'{"a": 1}')
        self.assertTrue(any(path.startswith("/mirror/") for path, _ in self.server.requests))

    def test_tampered_part_is_rejected_and_nothing_is_installed(self):
        self.server.files["/rel/s.zip.002"] = b"tampered" + self.parts["s.zip.002"][8:]
        with self.assertRaises(ValueError):
            self.installer().run()
        self.assertIsNone(install.installed_service(self.root))


    def test_uninstall_frees_space_and_leaves_nothing_installed(self):
        self.installer().run()
        freed = install.uninstall(self.root)
        self.assertGreater(freed, 5000)
        self.assertIsNone(install.installed_service(self.root))
        self.assertEqual(install.uninstall(self.root), 0)

    def test_service_update_reuses_unchanged_model_and_removes_old_version(self):
        old = self.installer().run()
        self.server.requests.clear()
        self.release = install.TranscriberRelease(**{**self.release.__dict__, "version": "1.1.0"})
        new = self.installer().run()
        self.assertEqual(new.version, "1.1.0")
        self.assertFalse(any("/resolve/" in path for path, _ in self.server.requests))
        self.assertFalse(old.exe.exists())
        self.assertTrue(new.exe.exists())


class ManifestTests(unittest.TestCase):
    def test_fetch_release_accepts_only_validly_signed_manifest(self):
        payload = json.dumps({
            "kind": "subtitleflow-transcriber", "version": "1.0.0", "api_version": 1, "platform": "windows-x86_64",
            "parts": [{"name": "s.zip.001", "size": 1, "sha256": "a" * 64}], "archive_sha256": "b" * 64, "archive_size": 1,
            "entry": "S/S.exe", "model": {"repo": "o/m", "revision": REVISION, "files": {"c.json": {"size": 1, "sha256": "c" * 64}}},
        }).encode()
        key = SigningKey.generate()
        envelope = {"key_id": "test", "payload": base64.b64encode(payload).decode(),
                    "signature": base64.b64encode(key.sign(payload).signature).decode()}
        seen = []

        def opener(url, timeout):
            seen.append(url)
            return io.BytesIO(json.dumps(envelope).encode())

        with patch.dict(update_trust.TRUSTED_UPDATE_KEYS, {"test": key.verify_key.encode().hex()}):
            release = install.fetch_release("owner/repo", opener=opener)
            self.assertEqual((release.version, release.model_revision), ("1.0.0", REVISION))
            self.assertEqual(seen, [f"https://github.com/owner/repo/releases/download/{install.TRANSCRIBER_TAG}/transcriber.json"])
            envelope["signature"] = base64.b64encode(b"\0" * 64).decode()
            with self.assertRaises(ValueError):
                install.fetch_release("owner/repo", opener=opener)

    def test_manifest_with_unsafe_paths_is_rejected(self):
        for bad in ({"entry": "../evil.exe"}, {"parts": [{"name": "..\\x", "size": 1, "sha256": "a" * 64}]}):
            data = {"kind": "subtitleflow-transcriber", "version": "1.0.0", "api_version": 1, "platform": "windows-x86_64",
                    "parts": [{"name": "s.zip.001", "size": 1, "sha256": "a" * 64}], "archive_sha256": "b" * 64,
                    "archive_size": 1, "entry": "S/S.exe",
                    "model": {"repo": "o/m", "revision": REVISION, "files": {"c.json": {"size": 1, "sha256": "c" * 64}}}}
            data.update(bad)
            with self.assertRaises(ValueError):
                install.parse_manifest(data)


if __name__ == "__main__":
    unittest.main()
