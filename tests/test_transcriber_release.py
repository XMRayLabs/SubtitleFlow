import base64
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from nacl.signing import SigningKey

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import transcriber_release  # noqa: E402
from subtitleflow import update_trust, updates  # noqa: E402


class TranscriberReleaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def test_split_parts_stay_under_limit_and_reassemble_exactly(self):
        archive = self.dir / "service.zip"
        content = bytes(range(256)) * 41   # 10496 bytes
        archive.write_bytes(content)
        parts = transcriber_release.split(archive, self.dir / "out", part_size=4000)
        self.assertEqual([p.name for p in parts], ["service.zip.001", "service.zip.002", "service.zip.003"])
        self.assertTrue(all(p.stat().st_size <= 4000 for p in parts))
        self.assertEqual(b"".join(p.read_bytes() for p in parts), content)

    def test_model_files_skip_download_cache_but_not_models_stored_under_a_cache_folder(self):
        model = self.dir / ".cache" / "huggingface" / "snapshot"
        (model / ".cache" / "huggingface").mkdir(parents=True)
        (model / ".cache" / "huggingface" / "lock").write_text("", encoding="utf-8")
        (model / "config.json").write_text("{}", encoding="utf-8")
        self.assertEqual(list(transcriber_release.model_files(model)), ["config.json"])

    def test_signed_manifest_lists_parts_and_model_files_and_detects_tampering(self):
        parts = [self.dir / "s.zip.001", self.dir / "s.zip.002"]
        parts[0].write_bytes(b"first")
        parts[1].write_bytes(b"second")
        model = self.dir / "model"
        (model / "sub").mkdir(parents=True)
        (model / "config.json").write_text("{}", encoding="utf-8")
        (model / "sub" / "code.py").write_text("x = 1", encoding="utf-8")
        payload = transcriber_release.payload("1.0.0", parts, "SubtitleFlow-Transcriber/SubtitleFlow-Transcriber.exe",
                                              "OpenMOSS-Team/MOSS-Transcribe-Diarize", "e8681d68e7042738ffca8ac8212bc8fcb1131ab8", model)
        key = SigningKey.generate()
        with patch.dict(update_trust.TRUSTED_UPDATE_KEYS, {"test": key.verify_key.encode().hex()}):
            envelope = transcriber_release.sign(payload, "test", key.encode().hex())
            data = updates.verify_manifest(json.dumps(envelope))
            self.assertEqual(data["parts"], [
                {"name": "s.zip.001", "size": 5, "sha256": hashlib.sha256(b"first").hexdigest()},
                {"name": "s.zip.002", "size": 6, "sha256": hashlib.sha256(b"second").hexdigest()},
            ])
            self.assertEqual(data["archive_sha256"], hashlib.sha256(b"firstsecond").hexdigest())
            self.assertEqual(data["model"]["files"]["sub/code.py"], {"size": 5, "sha256": hashlib.sha256(b"x = 1").hexdigest()})
            self.assertEqual(data["api_version"], 1)
            tampered = json.loads(base64.b64decode(envelope["payload"]))
            tampered["parts"][0]["sha256"] = "0" * 64
            envelope["payload"] = base64.b64encode(json.dumps(tampered).encode()).decode()
            with self.assertRaises(ValueError):
                updates.verify_manifest(json.dumps(envelope))

    def test_unsigned_payload_is_kept_for_offline_signing_and_never_left_beside_a_signed_manifest(self):
        out = self.dir / "out"
        with patch.object(transcriber_release, "signing_key", return_value=(None, None)):
            transcriber_release.write_manifest(b'{"kind":"test"}', out, use_local_key=False)
        unsigned = out / transcriber_release.PAYLOAD_NAME
        # 构建机没有密钥，但哈希只有它算得出来，所以保留 payload 供离线签名
        self.assertEqual(unsigned.read_bytes(), b'{"kind":"test"}')
        self.assertFalse((out / "transcriber.json").exists())

        key = SigningKey.generate()
        with patch.dict(update_trust.TRUSTED_UPDATE_KEYS, {"test": key.verify_key.encode().hex()}), \
                patch.object(transcriber_release, "signing_key", return_value=("test", key.encode().hex())):
            transcriber_release.main(["--sign-payload", str(unsigned), "--output", str(out), "--use-local-key"])
            signed = json.loads((out / "transcriber.json").read_text(encoding="utf-8"))
            self.assertEqual(updates.verify_manifest(json.dumps(signed)), {"kind": "test"})
        # 签好之后未签名副本必须消失，避免误当成清单上传
        self.assertFalse(unsigned.exists())

    def test_packaging_arguments_are_only_required_when_not_signing_an_existing_payload(self):
        with self.assertRaises(SystemExit):
            transcriber_release.main(["--output", str(self.dir / "out")])


if __name__ == "__main__":
    unittest.main()
