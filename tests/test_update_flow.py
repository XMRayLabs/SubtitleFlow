import hashlib
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from subtitleflow import updates


class UpdateFlowTests(unittest.TestCase):
    def test_new_release_manifest_and_successful_download(self):
        repo = "owner/subtitleflow"
        url = f"https://github.com/{repo}/releases/download/v9.0.0/setup.exe"
        payload = b"test-installer"
        digest = hashlib.sha256(payload).hexdigest()
        latest = {"tag_name": "v9.0.0", "assets": [{"name": "update.json", "browser_download_url":
                   f"https://github.com/{repo}/releases/download/v9.0.0/update.json"}]}
        manifest = {"version": "9.0.0", "platforms": {"windows-x86_64": {"url": url, "sha256": digest}}}
        with patch("subtitleflow.updates.platform_key", return_value="windows-x86_64"), patch("subtitleflow.updates.platform.system", return_value="Windows"), patch(
                "urllib.request.urlopen", side_effect=[io.BytesIO(json.dumps(latest).encode()), io.BytesIO(json.dumps(manifest).encode())]):
            release = updates.check(repo, current="1.0.0")
        with tempfile.TemporaryDirectory() as tmp, patch("urllib.request.urlopen", return_value=io.BytesIO(payload)):
            destination = updates.download(release, Path(tmp), threading.Event())
            self.assertEqual(destination.read_bytes(), payload)

    def test_prerelease_is_ignored(self):
        with patch("urllib.request.urlopen", return_value=io.BytesIO(b'{"prerelease":true}')):
            self.assertIsNone(updates.check("owner/repo"))

    def test_download_cancel_preserves_existing_installer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "setup.exe").write_bytes(b"existing")
            cancel = threading.Event()
            cancel.set()
            release = updates.Release("9.0.0", "https://github.com/owner/repo/releases/download/v9/setup.exe", "0" * 64, "setup.exe")
            with patch("urllib.request.urlopen", return_value=io.BytesIO(b"new")):
                with self.assertRaises(RuntimeError):
                    updates.download(release, root, cancel)
            self.assertEqual((root / "setup.exe").read_bytes(), b"existing")
            self.assertFalse((root / "setup.exe.download").exists())


if __name__ == "__main__":
    unittest.main()
