"""转录前的人声增强：先降噪、再去混响，适合只有人声与环境噪音的录音（没有音乐，不需要人声分离）。

两个模型都是 Mel-Band RoFormer（见 roformer.py），在 44.1 kHz 立体声上工作。权重第一次使用时
才下载（各约 0.9 GB），按文件固定的大小与 SHA-256 校验：权重用 torch.load 读取，哈希写死在
服务里，与服务本身一起受发布签名保护。

这里只有静态数据和下载逻辑；torch 到 load() 时才导入。
"""
from dataclasses import dataclass
import gc
import hashlib
from pathlib import Path
import urllib.error
import urllib.request

SAMPLE_RATE = 44100
CHANNELS = 2
CHUNK = 352800          # 模型训练时的片段长度（8 秒）
OVERLAP = 2             # 相邻片段重叠一半，接缝处交叉淡化
FADE = CHUNK // 10
DOWNLOAD_BLOCK = 1024 * 1024
DOWNLOAD_TIMEOUT = 30


@dataclass(frozen=True)
class Weights:
    name: str
    size: int
    sha256: str
    urls: tuple[str, ...]


DEREVERB_REVISION = "cef05ad2b5b3145ea5c149d3ad5d1f8439b34d06"
# 顺序即处理顺序：先去掉环境噪音，去混响模型才不会把噪音当成混响的一部分
MODELS = (
    # aufr33 的降噪模型，普通版（aggressive 版更伤人声）
    Weights("denoise_mel_band_roformer_aufr33_sdr_27.9959.ckpt", 913097300,
            "7c1c39191edc34e942ca7f2346ce6b6c0e1208a5f76349ffce6f696bd12910de",
            ("https://github.com/nomadkaraoke/python-audio-separator/releases/download/model-configs/"
             "denoise_mel_band_roformer_aufr33_sdr_27.9959.ckpt",)),
    # anvuew 的去混响模型，mono 版：训练数据是单人说话，且修正了普通版训练时混响未对齐的问题，
    # 去混响更强；普通版多出的"去除乐器残留"能力对没有音乐的录音用不上
    Weights("dereverb_mel_band_roformer_mono_anvuew_sdr_20.4029.ckpt", 913097978,
            "f099ee717eb57fb0ad5eb0e7c9ad6787c36168140b61ce2b158b90c2c4ecee79",
            tuple(f"{host}/anvuew/dereverb_mel_band_roformer/resolve/{DEREVERB_REVISION}/"
                  "dereverb_mel_band_roformer_mono_anvuew_sdr_20.4029.ckpt"
                  for host in ("https://huggingface.co", "https://hf-mirror.com"))),
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(DOWNLOAD_BLOCK), b""):
            digest.update(block)
    return digest.hexdigest()


class Enhancer:
    def __init__(self, folder, models=MODELS, opener=urllib.request.urlopen):
        self.folder, self.weights, self.opener = Path(folder), models, opener
        self.models = []

    @property
    def loaded(self):
        return bool(self.models)

    def _ready(self, weights: Weights):
        # 只比大小：每次转录都重算 1.8 GB 的哈希太慢；下载完成时已校验过，之后只会被截断或误删
        path = self.folder / weights.name
        return path.is_file() and path.stat().st_size == weights.size

    def missing_bytes(self) -> int:
        """还要下载多少字节；为 0 表示权重已就绪。"""
        return sum(w.size for w in self.weights if not self._ready(w))

    def download(self, on_progress, should_stop):
        """下载缺少的权重，断点续传。on_progress(本次已下载字节)；should_stop() 为真时尽快返回。"""
        done = 0
        for weights in self.weights:
            if self._ready(weights):
                continue
            self.folder.mkdir(parents=True, exist_ok=True)
            partial = self.folder / (weights.name + ".partial")
            start, error = done, None
            for url in weights.urls:
                try:
                    done = start + self._fetch(url, partial, weights.size,
                                               lambda n, base=start: on_progress(base + n), should_stop)
                    break
                except (urllib.error.URLError, OSError) as exc:
                    error = exc
            else:
                raise RuntimeError(f"降噪模型下载失败：{error}")
            if should_stop():
                return
            if partial.stat().st_size != weights.size or _sha256(partial) != weights.sha256:
                partial.unlink(missing_ok=True)
                raise RuntimeError(f"{weights.name} 校验失败，已删除，请重试")
            partial.replace(self.folder / weights.name)

    def _fetch(self, url, partial: Path, size, on_progress, should_stop) -> int:
        """把 url 续传到 partial，返回 partial 现有的字节数（含之前已下好的部分），供进度累计。"""
        have = partial.stat().st_size if partial.exists() else 0
        if have > size:
            partial.unlink()
            have = 0
        fresh = 0
        if have == size:
            return have      # 上次已下完、只差改名：整份都算进进度，否则总进度到不了 100%
        request = urllib.request.Request(url, headers={"Range": f"bytes={have}-"} if have else {})
        with self.opener(request, timeout=DOWNLOAD_TIMEOUT) as response:
            if have and response.status != 206:   # 服务器不支持续传，从头下载
                have = 0
            with partial.open("ab" if have else "wb") as handle:
                on_progress(have)
                while not should_stop():
                    block = response.read(DOWNLOAD_BLOCK)
                    if not block:
                        break
                    handle.write(block)
                    fresh += len(block)
                    on_progress(have + fresh)
        if not should_stop() and have + fresh < size:
            # 提前断流不会抛异常；保留 .partial 并换下一个地址续传，别当成下载完成去校验、删除
            raise OSError(f"下载中断：只收到 {have + fresh} / {size} 字节")
        return have + fresh

    def load(self):
        import torch
        from torch import nn
        from .roformer import MelBandRoformer
        if not torch.cuda.is_available():
            raise RuntimeError("没有可用的 NVIDIA 显卡（CUDA）")
        models = []
        for weights in self.weights:
            model = MelBandRoformer()
            model.load_state_dict(torch.load(self.folder / weights.name, map_location="cpu", weights_only=True))
            # 线性层存成半精度，显存减半（每个模型约 0.5 GB）；旋转位置编码与归一化保持单精度
            for module in model.modules():
                if isinstance(module, nn.Linear):
                    module.half()
            models.append(model.cuda().eval())
        self.models = models

    def unload(self):
        self.models = []
        gc.collect()
        import torch
        torch.cuda.empty_cache()

    def enhance(self, audio, should_stop):
        """audio：44.1 kHz 立体声，形状 (采样数, 2) 的 float32 数组；返回同形状的增强结果。
        should_stop() 为真时尽快返回（结果作废，由调用方丢弃）。"""
        import numpy as np
        import torch
        signal = torch.from_numpy(np.ascontiguousarray(audio.T)).cuda()
        try:
            with torch.inference_mode(), torch.autocast("cuda", dtype=torch.float16):
                for model in self.models:
                    if should_stop():
                        break
                    signal = self._separate(model, signal, should_stop)
        except RuntimeError as exc:
            if "out of memory" not in str(exc).lower():
                raise
            raise RuntimeError("显存不足，无法降噪与去混响，请关闭该选项或调短分段时长") from exc
        result = signal.float().T.cpu().numpy()
        del signal
        torch.cuda.empty_cache()
        return result

    @staticmethod
    def _separate(model, signal, should_stop):
        """按训练时的片段长度滑窗处理整段音频，重叠部分按淡入淡出窗加权平均。"""
        import torch
        import torch.nn.functional as F
        length = signal.shape[-1]
        step = CHUNK // OVERLAP
        border = CHUNK - step
        # 两端先镜像补一截，让首尾也落在片段中间、有完整的上下文
        padded = length > 2 * border
        if padded:
            signal = F.pad(signal[None], (border, border), mode="reflect")[0]
        total = signal.shape[-1]
        fade = torch.linspace(0, 1, FADE, device=signal.device)
        result = torch.zeros_like(signal)
        weight = torch.zeros(total, device=signal.device)
        for start in range(0, total, step):
            if should_stop():
                break
            part = signal[:, start:start + CHUNK]
            size = part.shape[-1]
            if size < CHUNK:
                mode = "reflect" if size > CHUNK // 2 else "constant"
                part = F.pad(part[None], (0, CHUNK - size), mode=mode)[0]
            window = torch.ones(CHUNK, device=signal.device)
            if start > 0:
                window[:FADE] = fade
            if start + step < total:
                window[-FADE:] = fade.flip(0)
            out = model(part[None])[0, :, :size].float()
            result[:, start:start + size] += out * window[:size]
            weight[start:start + size] += window[:size]
        result /= weight.clamp(min=1e-8)
        return result[:, border:-border] if padded else result
