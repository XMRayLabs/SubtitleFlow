"""降噪与去混响：权重下载与校验、分段重新解码后的对齐、网络结构。不需要显卡，也不下载真实权重。"""
import hashlib
import io
from pathlib import Path
import shutil
import tempfile
import unittest
import urllib.error
import wave

from transcriber.enhance import Enhancer, Weights
from transcriber.media import FfmpegMedia, SAMPLE_RATE
from transcriber.segments import Segment

try:
    import numpy as np
    import torch
    AVAILABLE = True
except Exception:                       # pragma: no cover - 主程序的构建环境没有 torch
    AVAILABLE = False


class Response(io.BytesIO):
    def __init__(self, data, status):
        super().__init__(data)
        self.status = status


class Opener:
    """按地址返回内容，支持 Range；failing 中的地址直接报网络错误。"""

    def __init__(self, files, failing=()):
        self.files, self.failing, self.requests = files, set(failing), []

    def __call__(self, request, timeout=None):
        url, first = request.full_url, request.get_header("Range")
        self.requests.append((url, first))
        if url in self.failing:
            raise urllib.error.URLError("unreachable")
        data = self.files[url]
        if first:
            return Response(data[int(first.split("=")[1].rstrip("-")):], 206)
        return Response(data, 200)


def weights(name, data, *urls):
    return Weights(name, len(data), hashlib.sha256(data).hexdigest(), urls)


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.data = bytes(range(256)) * 20

    def test_downloads_missing_weights_and_then_reports_nothing_missing(self):
        spec = weights("a.ckpt", self.data, "https://hf/a")
        enhancer = Enhancer(self.dir, (spec,), Opener({"https://hf/a": self.data}))
        self.assertEqual(enhancer.missing_bytes(), len(self.data))
        seen = []
        enhancer.download(seen.append, lambda: False)
        self.assertEqual((self.dir / "a.ckpt").read_bytes(), self.data)
        self.assertEqual(seen[-1], len(self.data))
        self.assertEqual(enhancer.missing_bytes(), 0)

    def test_interrupted_download_resumes_from_where_it_stopped(self):
        (self.dir / "a.ckpt.partial").write_bytes(self.data[:1000])
        opener = Opener({"https://hf/a": self.data})
        Enhancer(self.dir, (weights("a.ckpt", self.data, "https://hf/a"),), opener).download(lambda n: None, lambda: False)
        self.assertEqual(opener.requests, [("https://hf/a", "bytes=1000-")])
        self.assertEqual((self.dir / "a.ckpt").read_bytes(), self.data)

    def test_falls_back_to_the_mirror(self):
        spec = weights("a.ckpt", self.data, "https://hf/a", "https://mirror/a")
        opener = Opener({"https://mirror/a": self.data}, failing={"https://hf/a"})
        Enhancer(self.dir, (spec,), opener).download(lambda n: None, lambda: False)
        self.assertEqual((self.dir / "a.ckpt").read_bytes(), self.data)

    def test_tampered_file_is_deleted_instead_of_loaded(self):
        spec = weights("a.ckpt", self.data, "https://hf/a")
        tampered = b"x" + self.data[1:]
        enhancer = Enhancer(self.dir, (spec,), Opener({"https://hf/a": tampered}))
        with self.assertRaisesRegex(RuntimeError, "校验失败"):
            enhancer.download(lambda n: None, lambda: False)
        self.assertEqual(list(self.dir.iterdir()), [])

    def test_stopping_keeps_the_partial_file_for_next_time(self):
        spec = weights("a.ckpt", self.data, "https://hf/a")
        Enhancer(self.dir, (spec,), Opener({"https://hf/a": self.data})).download(lambda n: None, lambda: True)
        self.assertFalse((self.dir / "a.ckpt").exists())
        self.assertTrue((self.dir / "a.ckpt.partial").exists())


@unittest.skipUnless(AVAILABLE and shutil.which("ffmpeg"), "需要 numpy 与 ffmpeg")
class EnhancedClipTests(unittest.TestCase):
    def test_enhanced_clip_lines_up_with_the_plain_clip(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "tone.wav"
        rate, seconds = 22050, 7
        t = np.arange(rate * seconds) / rate
        samples = (0.3 * np.sin(2 * np.pi * 220 * t) * 32767).astype("<i2")
        with wave.open(str(path), "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(rate)
            out.writeframes(samples.tobytes())
        media = FfmpegMedia()
        source, duration, silences = media.probe(str(path))
        segment = Segment(2.5, 5.25, False)   # 不在整秒上，检验 -ss/-t 的取整
        seen = []

        def identity(hifi):
            seen.append(hifi.shape)
            return hifi

        plain = media.clip(source, segment)
        enhanced = media.clip(source, segment, identity)
        # 增强模型拿到的是 44.1k 立体声
        self.assertEqual(seen[0][1], 2)
        self.assertAlmostEqual(seen[0][0] / 44100, segment.end - segment.start, delta=0.01)
        # 转回 16k 单声道后与不增强时逐采样对齐（只差重采样误差），时间戳才不会漂移
        self.assertEqual(enhanced.shape, plain.shape)
        self.assertEqual(plain.size, round((segment.end - segment.start) * SAMPLE_RATE))
        self.assertLess(float(np.abs(enhanced[800:-800] - plain[800:-800]).max()), 0.02)


@unittest.skipUnless(AVAILABLE, "torch 未安装")
class RoformerTests(unittest.TestCase):
    def test_mel_bands_match_librosa(self):
        from transcriber.roformer import mel_filter_bank
        try:
            from librosa.filters import mel
        except Exception:
            self.skipTest("librosa 未安装")
        self.assertTrue(np.allclose(mel_filter_bank(44100, 2048, 60), mel(sr=44100, n_fft=2048, n_mels=60), atol=1e-7))

    def test_output_has_the_shape_of_the_input(self):
        from transcriber.roformer import MelBandRoformer
        model = MelBandRoformer(dim=16, depth=1, heads=2, dim_head=8).eval()
        with torch.inference_mode():
            out = model(torch.randn(1, 2, 44100))
        self.assertEqual(tuple(out.shape), (1, 2, 44100))

    def test_submodule_names_match_the_published_checkpoints(self):
        from transcriber.roformer import MelBandRoformer
        keys = set(MelBandRoformer(dim=16, depth=6, heads=2, dim_head=8).state_dict())
        # 摘自公开权重文件的键名；名字对不上就加载不了
        for key in ("layers.5.1.layers.0.0.rotary_embed.freqs", "layers.0.0.layers.0.0.to_gates.bias",
                    "layers.0.0.layers.0.1.net.4.weight", "band_split.to_features.59.0.gamma",
                    "mask_estimators.0.to_freqs.59.0.4.bias"):
            self.assertIn(key, keys)


if __name__ == "__main__":
    unittest.main()
