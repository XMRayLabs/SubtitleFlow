"""MOSS-Transcribe-Diarize 模型。torch / transformers 只在 load() 时才导入。"""
import gc
import time

from .engines import EngineInfo
from .segments import latest_timestamp, parse_output

MODEL_ID = "OpenMOSS-Team/MOSS-Transcribe-Diarize"
REVISION = "e8681d68e7042738ffca8ac8212bc8fcb1131ab8"  # 固定住，远程代码会变；与发布清单中的 revision 一致
MAX_NEW_TOKENS = 8192   # 每个转录分段的生成上限（1 分钟约 400~500 token）
PROGRESS_INTERVAL = 1.0  # 解码已生成内容、上报进度的最短间隔（秒）
PROMPT = ("请将音频转写为文本，每一段需以起始时间戳和说话人编号（[S01]、[S02]、[S03]…）开头，"
          "正文为对应的语音内容，并在段末标注结束时间戳，以清晰标明该段语音范围。")


class MossEngine:
    def __init__(self, model=MODEL_ID, revision=REVISION):
        self.model_source, self.revision = model, revision
        self.model = self.processor = self.prompt = None

    @classmethod
    def info(cls) -> EngineInfo:
        # 模型固定、语言由模型自行判断，因此两项都只有一个取值
        return EngineInfo(name="moss", models=(MODEL_ID,), default_model=MODEL_ID,
                          languages=("auto",), diarization=True)

    @property
    def loaded(self):
        return self.model is not None

    def load(self):
        import torch
        from transformers import AutoModelForCausalLM, AutoProcessor
        if not torch.cuda.is_available():
            raise RuntimeError("没有可用的 NVIDIA 显卡（CUDA）")
        self.model = AutoModelForCausalLM.from_pretrained(
            self.model_source, revision=self.revision, trust_remote_code=True,
            dtype="auto", attn_implementation="sdpa",
        ).to(torch.bfloat16).cuda().eval()
        self.processor = AutoProcessor.from_pretrained(self.model_source, revision=self.revision, trust_remote_code=True)
        messages = [{"role": "user", "content": [{"type": "audio", "audio": ""}, {"type": "text", "text": PROMPT}]}]
        self.prompt = self.processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)

    def unload(self):
        self.model = self.processor = self.prompt = None
        gc.collect()
        import torch
        torch.cuda.empty_cache()

    def transcribe(self, audio, on_progress, should_stop):
        """返回本分段的字幕，时间相对分段开头（平移由服务层做）。

        on_progress(秒) 上报已转写到分段内第几秒；should_stop() 为真时在下一步生成处停止
        （结果作废，由调用方丢弃）。
        """
        import torch
        from transformers import StoppingCriteria, StoppingCriteriaList
        from transformers.generation.streamers import BaseStreamer
        tokenizer = self.processor.tokenizer

        class Stop(StoppingCriteria):
            def __call__(self, input_ids, scores, **kwargs):
                return torch.full((input_ids.shape[0],), should_stop(), device=input_ids.device, dtype=torch.bool)

        class Progress(BaseStreamer):
            def __init__(self):
                self.ids, self.prompt_seen, self.last = [], False, 0.0

            def put(self, value):
                # generate() 第一次 put 的是 prompt，之后才是逐个生成的 token
                if not self.prompt_seen:
                    self.prompt_seen = True
                    return
                self.ids.extend(value.reshape(-1).tolist())
                if time.time() - self.last >= PROGRESS_INTERVAL:
                    self.last = time.time()
                    # 进度来自已生成内容里的最后一个时间戳，是 MOSS 输出格式的细节，不外泄给服务层
                    stamp = latest_timestamp(tokenizer.decode(self.ids, skip_special_tokens=True))
                    if stamp is not None:
                        on_progress(stamp)

            def end(self):
                pass

        inputs = self.processor(text=self.prompt, audio=[audio], return_tensors="pt").to("cuda")
        prompt_length = inputs["input_ids"].shape[1]
        try:
            with torch.inference_mode():
                out = self.model.generate(**inputs, max_new_tokens=MAX_NEW_TOKENS, do_sample=False, streamer=Progress(),
                                          stopping_criteria=StoppingCriteriaList([Stop()]))
        except torch.OutOfMemoryError:
            raise RuntimeError("显存不足，请调短分段时长后重试")
        finally:
            del inputs
            torch.cuda.empty_cache()
        raw = self.processor.batch_decode(out[:, prompt_length:], skip_special_tokens=True)[0]
        return parse_output(raw, 0.0)
