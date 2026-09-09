import base64
import hashlib
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from subtitleflow import updates
from subtitleflow.safety import bounded_response, atomic_write, read_bytes, safe_tree
from subtitleflow.jobs import Job, JobOptions
from subtitleflow.api import APIConfig

class SecurityTests(unittest.TestCase):
    def test_manifest_signature(self):
        key = Ed25519PrivateKey.generate()
        payload = b'{"version":"9.0.0"}'
        signed = {"key_id":"test", "payload":base64.b64encode(payload).decode(),
                  "signature":base64.b64encode(key.sign(payload)).decode()}
        with self.assertRaises(ValueError): updates.verify_manifest(json.dumps(signed))
        with patch.dict(updates.TRUSTED_UPDATE_KEYS, {"test":key.public_key().public_bytes_raw().hex()}):
            self.assertEqual(updates.verify_manifest(json.dumps(signed))["version"],"9.0.0")
            signed["payload"] = base64.b64encode(b'{"version":"9.0.1"}').decode()
            with self.assertRaises(ValueError): updates.verify_manifest(json.dumps(signed))

    def test_install_tamper(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'setup.exe';path.write_bytes(b'original')
            saved = updates.Downloaded(path, hashlib.sha256(b'original').hexdigest())
            self.assertEqual(updates.verify_install(saved),path)
            path.write_bytes(b'tampered')
            with self.assertRaises(ValueError): updates.verify_install(saved)

    def test_limits_cancel_and_deadline(self):
        with self.assertRaises(ValueError): bounded_response(io.BytesIO(b'12345'),4)
        self.assertEqual(bounded_response(io.BytesIO(b'1234'),4),b'1234')
        cancel=threading.Event();cancel.set()
        with self.assertRaises(RuntimeError): bounded_response(io.BytesIO(b'x'),cancel=cancel)
        with self.assertRaises(ValueError): bounded_response(io.BytesIO(b'x'),seconds=-1)
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'large';p.write_bytes(b'12345')
            with self.assertRaises(ValueError):read_bytes(p,4)

    def test_download_size_limit(self):
        with tempfile.TemporaryDirectory() as tmp, patch('subtitleflow.updates.open_update',return_value=io.BytesIO(b'12345')), patch.object(updates,'MAX_INSTALLER',4):
            with self.assertRaises(ValueError):
                updates.download(updates.Release('1.0.0','https://github.com/a/b/releases/download/v1/a.exe','0'*64,'a.exe'),Path(tmp),threading.Event())
            self.assertEqual(list(Path(tmp).iterdir()),[])

    def test_resume_never_reads_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);source=base/'a.srt';source.write_text('1\n00:00:00,000 --> 00:00:01,000\nCanary\n')
            options=JobOptions(mode='merge');api=APIConfig('','')
            root=Job([source],base/'out',options,api,threading.Event()).run()
            report=json.loads((root/'report.json').read_text(encoding='utf-8'))
            report['files'][0]['status']='pending'
            (root/'report.json').write_text(json.dumps(report))
            (root/'originals/a.srt').unlink()
            with patch('subtitleflow.jobs.read_bytes',side_effect=AssertionError('external read')):
                Job([],base/'out',options,api,threading.Event(),resume=root).run()
            self.assertFalse((root/'originals/a.srt').exists())
            self.assertEqual(json.loads((root/'report.json').read_text(encoding='utf-8'))['files'][0]['status'],'failed')

    def test_link_write_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);outside=root/'outside';outside.write_bytes(b'keep')
            link=root/'link'
            try:link.symlink_to(outside)
            except OSError:self.skipTest('symlink privilege unavailable')
            with self.assertRaises(ValueError):atomic_write(link,b'changed')
            with self.assertRaises(ValueError):safe_tree(root)
            self.assertEqual(outside.read_bytes(),b'keep')
