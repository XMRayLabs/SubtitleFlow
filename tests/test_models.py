import io
import json
import ssl
import threading
import unittest
from unittest.mock import patch
from subtitleflow.api import APIConfig, Client, APIError


class ModelsTests(unittest.TestCase):
    def test_root_and_explicit_paths(self):
        self.assertEqual(APIConfig("https://127.0.0.1:8081/", "", "m").endpoint(), "https://127.0.0.1:8081/v1/chat/completions")
        self.assertEqual(APIConfig("https://host/custom/v2/", "", "m").models_endpoint(), "https://host/custom/v2/models")
        self.assertEqual(APIConfig("https://host/v1/chat/completions", "", "").models_endpoint(), "https://host/v1/models")

    def test_tls_opt_in_is_local_only(self):
        self.assertFalse(APIConfig("https://localhost:8081", "").allow_local_self_signed)
        with self.assertRaises(ValueError):
            APIConfig("https://example.com", "", allow_local_self_signed=True).base()
        APIConfig("https://[::1]:8081", "", allow_local_self_signed=True).base()

    def test_models_no_selected_model_required(self):
        response = io.BytesIO(json.dumps({"data": [{"id": "a"}, {"id": "b"}, {"id": "a"}]}).encode())
        with patch("urllib.request.OpenerDirector.open", return_value=response):
            self.assertEqual(Client(APIConfig("https://host", ""), threading.Event()).models(), ["a", "b"])

    def test_certificate_error_is_clear_and_not_retried(self):
        with patch("urllib.request.OpenerDirector.open", side_effect=ssl.SSLCertVerificationError("test")) as opened:
            with self.assertRaisesRegex(APIError, "证书验证失败"):
                Client(APIConfig("https://localhost", "", "m"), threading.Event()).chat("x", "y")
            self.assertEqual(opened.call_count, 1)


if __name__ == "__main__":
    unittest.main()
