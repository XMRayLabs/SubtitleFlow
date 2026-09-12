"""MossEngine 的契约测试：用桩替换模型与处理器，不需要显卡也不下载模型。

这里只验证引擎自己那一层——把模型原始输出解析成分段内的 Cue，并按已生成内容上报进度秒数。
真正的模型推理不在测试范围内。
"""
import unittest
from unittest.mock import patch

try:
    import torch
    import transformers  # noqa: F401  仅用于判断能否运行本组测试
    AVAILABLE = True
except Exception:                       # pragma: no cover - 主程序的构建环境没有 torch
    AVAILABLE = False


class Inputs(dict):
    """processor(...) 的返回值：支持 .to(device) 和 ** 解包。"""

    def to(self, device):
        return self


class FakeTokenizer:
    def __init__(self, pieces):
        self.pieces = pieces

    def decode(self, ids, skip_special_tokens=True):
        return "".join(self.pieces[i] for i in ids)


class FakeProcessor:
    def __init__(self, pieces):
        self.tokenizer = FakeTokenizer(pieces)

    def __call__(self, text=None, audio=None, return_tensors=None):
        return Inputs(input_ids=torch.zeros((1, 4), dtype=torch.long))

    def batch_decode(self, out, skip_special_tokens=True):
        return ["".join(self.tokenizer.pieces)]


class FakeModel:
    """按 transformers 的约定 put 一次 prompt，再逐块 put 生成内容。"""

    def __init__(self, chunks):
        self.chunks = chunks

    def generate(self, input_ids=None, max_new_tokens=None, do_sample=None, streamer=None, stopping_criteria=None):
        streamer.put(torch.zeros((1, 4), dtype=torch.long))     # prompt，应被忽略
        for chunk in self.chunks:
            streamer.put(torch.tensor(chunk, dtype=torch.long))
        streamer.end()
        return torch.zeros((1, 8), dtype=torch.long)


@unittest.skipUnless(AVAILABLE, "torch/transformers 未安装")
class MossEngineContractTests(unittest.TestCase):
    def engine(self, pieces, chunks):
        from transcriber.engine import MossEngine
        engine = MossEngine()
        engine.prompt = "prompt"
        engine.processor = FakeProcessor(pieces)
        engine.model = FakeModel(chunks)
        return engine

    def run_transcribe(self, pieces, chunks):
        engine = self.engine(pieces, chunks)
        seen = []
        with patch("torch.cuda.empty_cache"), patch("transcriber.engine.PROGRESS_INTERVAL", 0):
            cues = engine.transcribe(object(), seen.append, lambda: False)
        return cues, seen

    def test_returns_segment_relative_cues_parsed_from_the_model_output(self):
        from transcriber.segments import Cue
        pieces = ["[0.00][S01] 你好[2.50]", "[2.50][S02] 再见[4.00]"]
        cues, _ = self.run_transcribe(pieces, [[0], [1]])
        # 时间相对分段开头，平移是服务层的事
        self.assertEqual(cues, [Cue(0, 2500, "你好"), Cue(2500, 4000, "再见")])

    def test_progress_reports_seconds_not_raw_text(self):
        pieces = ["[0.00][S01] 你好[2.50]", "[2.50][S02] 再见[4.00]"]
        _, seen = self.run_transcribe(pieces, [[0], [1]])
        self.assertTrue(seen, "应当上报过进度")
        self.assertTrue(all(isinstance(v, float) for v in seen), seen)
        self.assertEqual(seen[-1], 4.0)

    def test_output_without_usable_cues_yields_no_subtitles(self):
        cues, _ = self.run_transcribe(["模型没有按格式输出"], [[0]])
        self.assertEqual(cues, [])

    def failing_transcribe(self, error):
        engine = self.engine(["[0.00][S01] 你好[2.50]"], [[0]])
        engine.model.generate = lambda **kwargs: (_ for _ in ()).throw(error)
        with patch("torch.cuda.empty_cache"):
            with self.assertRaises(RuntimeError) as caught:
                engine.transcribe(object(), lambda seconds: None, lambda: False)
        return caught.exception

    def test_out_of_memory_is_explained_whatever_torch_calls_it(self):
        # 各 torch 版本的 OOM 例外类型不同，但都是 RuntimeError 的子类、消息都含 out of memory
        for error in (RuntimeError("CUDA out of memory. Tried to allocate 2.00 GiB"),
                      getattr(torch, "OutOfMemoryError", RuntimeError)("CUDA out of memory")):
            with self.subTest(type(error).__name__):
                self.assertIn("显存不足", str(self.failing_transcribe(error)))

    def test_other_runtime_errors_keep_their_own_message(self):
        self.assertIn("cuDNN", str(self.failing_transcribe(RuntimeError("cuDNN error"))))


if __name__ == "__main__":
    unittest.main()
