"""Mel-Band RoFormer 网络，只保留推理用到的部分，供降噪与去混响使用。

改写自 ZFTurbo/Music-Source-Separation-Training 的 models/bs_roformer/mel_band_roformer.py
（MIT License，Copyright (c) 2024 Roman Solovyev；其本身源自 lucidrains/BS-RoFormer，MIT License）。
去掉了训练、损失与可选结构，并用纯 torch / numpy 替换 einops、rotary-embedding-torch 与 librosa，
服务不必多带依赖。子模块的名字与原实现保持一致，公开的权重文件才能原样加载。

torch 由调用方在加载模型时才导入本模块（见 enhance.py），构建服务时不会导入。
"""
import numpy as np
import torch
from torch import nn
import torch.nn.functional as F


def mel_filter_bank(sample_rate, n_fft, n_mels):
    """与 librosa.filters.mel 默认参数（Slaney 刻度与归一化、float32）相同的滤波器组。"""
    f_sp, min_log_hz = 200.0 / 3, 1000.0
    min_log_mel, logstep = min_log_hz / f_sp, np.log(6.4) / 27.0

    def hz_to_mel(hz):
        return min_log_mel + np.log(hz / min_log_hz) / logstep if hz >= min_log_hz else hz / f_sp

    def mel_to_hz(mels):
        return np.where(mels >= min_log_mel, min_log_hz * np.exp(logstep * (mels - min_log_mel)), f_sp * mels)

    fft_freqs = np.linspace(0, sample_rate / 2, 1 + n_fft // 2)
    mel_f = mel_to_hz(np.linspace(hz_to_mel(0.0), hz_to_mel(sample_rate / 2), n_mels + 2))
    fdiff = np.diff(mel_f)
    ramps = np.subtract.outer(mel_f, fft_freqs)
    weights = np.zeros((n_mels, len(fft_freqs)), dtype=np.float32)
    for i in range(n_mels):
        weights[i] = np.maximum(0, np.minimum(-ramps[i] / fdiff[i], ramps[i + 2] / fdiff[i + 1]))
    weights *= (2.0 / (mel_f[2:n_mels + 2] - mel_f[:n_mels]))[:, None]
    return weights


class RMSNorm(nn.Module):
    def __init__(self, dim):
        super().__init__()
        self.scale = dim ** 0.5
        self.gamma = nn.Parameter(torch.ones(dim))

    def forward(self, x):
        return F.normalize(x, dim=-1) * self.scale * self.gamma


class RotaryEmbedding(nn.Module):
    """rotary-embedding-torch 的默认配置（相邻两维一组旋转，theta=10000）。freqs 存在权重文件里。"""

    def __init__(self, dim, theta=10000):
        super().__init__()
        freqs = 1.0 / (theta ** (torch.arange(0, dim, 2)[:dim // 2].float() / dim))
        self.freqs = nn.Parameter(freqs, requires_grad=False)

    def rotate(self, t):
        positions = torch.arange(t.shape[-2], device=t.device, dtype=self.freqs.dtype)
        angles = (positions[:, None] * self.freqs).repeat_interleave(2, dim=-1)
        pairs = t.unflatten(-1, (-1, 2))
        rotated = torch.stack((-pairs[..., 1], pairs[..., 0]), dim=-1).flatten(-2)
        return (t * angles.cos() + rotated * angles.sin()).type(t.dtype)


class Attention(nn.Module):
    def __init__(self, dim, heads, dim_head, rotary_embed):
        super().__init__()
        self.heads = heads
        self.rotary_embed = rotary_embed
        self.norm = RMSNorm(dim)
        self.to_qkv = nn.Linear(dim, heads * dim_head * 3, bias=False)
        self.to_gates = nn.Linear(dim, heads)
        self.to_out = nn.Sequential(nn.Linear(heads * dim_head, dim, bias=False))

    def forward(self, x):
        x = self.norm(x)
        batch, length, _ = x.shape
        q, k, v = self.to_qkv(x).view(batch, length, 3, self.heads, -1).permute(2, 0, 3, 1, 4)
        out = F.scaled_dot_product_attention(self.rotary_embed.rotate(q), self.rotary_embed.rotate(k), v)
        out = out * self.to_gates(x).transpose(1, 2).unsqueeze(-1).sigmoid()
        return self.to_out(out.transpose(1, 2).reshape(batch, length, -1))


class FeedForward(nn.Module):
    def __init__(self, dim, mult=4):
        super().__init__()
        # 下标与原实现一致（中间的 Dropout 推理时不起作用，但占着位置）
        self.net = nn.Sequential(RMSNorm(dim), nn.Linear(dim, dim * mult), nn.GELU(), nn.Dropout(0.0),
                                 nn.Linear(dim * mult, dim), nn.Dropout(0.0))

    def forward(self, x):
        return self.net(x)


class Transformer(nn.Module):
    def __init__(self, dim, depth, heads, dim_head, rotary_embed):
        super().__init__()
        self.layers = nn.ModuleList(nn.ModuleList([Attention(dim, heads, dim_head, rotary_embed), FeedForward(dim)])
                                    for _ in range(depth))
        self.norm = RMSNorm(dim)

    def forward(self, x):
        for attn, ff in self.layers:
            x = attn(x) + x
            x = ff(x) + x
        return self.norm(x)


class BandSplit(nn.Module):
    def __init__(self, dim, dim_inputs):
        super().__init__()
        self.dim_inputs = dim_inputs
        self.to_features = nn.ModuleList(nn.Sequential(RMSNorm(d), nn.Linear(d, dim)) for d in dim_inputs)

    def forward(self, x):
        return torch.stack([net(part) for part, net in zip(x.split(self.dim_inputs, dim=-1), self.to_features)], dim=-2)


class MaskEstimator(nn.Module):
    def __init__(self, dim, dim_inputs, depth, expansion=4):
        super().__init__()
        hidden = dim * expansion

        def mlp(dim_out):
            dims = (dim, *(hidden,) * depth, dim_out)
            layers = []
            for index, (d_in, d_out) in enumerate(zip(dims[:-1], dims[1:])):
                layers.append(nn.Linear(d_in, d_out))
                if index < len(dims) - 2:
                    layers.append(nn.Tanh())
            return nn.Sequential(*layers)

        self.to_freqs = nn.ModuleList(nn.Sequential(mlp(d * 2), nn.GLU(dim=-1)) for d in dim_inputs)

    def forward(self, x):
        return torch.cat([net(band) for band, net in zip(x.unbind(dim=-2), self.to_freqs)], dim=-1)


class MelBandRoformer(nn.Module):
    """输入 (batch, 2, 采样数) 的立体声，输出同形状的目标声部（降噪模型为干净人声，去混响模型为干声）。"""

    def __init__(self, dim=384, depth=6, num_bands=60, heads=8, dim_head=64, time_depth=1, freq_depth=1,
                 sample_rate=44100, n_fft=2048, hop_length=441, win_length=2048, mask_estimator_depth=2):
        super().__init__()
        self.channels = 2
        self.stft_kwargs = dict(n_fft=n_fft, hop_length=hop_length, win_length=win_length, normalized=False)
        time_rotary, freq_rotary = RotaryEmbedding(dim_head), RotaryEmbedding(dim_head)
        self.layers = nn.ModuleList(
            nn.ModuleList([Transformer(dim, time_depth, heads, dim_head, time_rotary),
                           Transformer(dim, freq_depth, heads, dim_head, freq_rotary)])
            for _ in range(depth))

        bank = torch.from_numpy(mel_filter_bank(sample_rate, n_fft, num_bands))
        # 与原实现相同：强制首尾频点归入首尾两个频带，保证每个频点都被覆盖
        bank[0][0] = bank[-1, -1] = 1.0
        freqs_per_band = bank > 0
        freqs = freqs_per_band.shape[1]
        freq_indices = torch.arange(freqs).repeat(num_bands, 1)[freqs_per_band]
        # 立体声的两个声道交错排在频率维上：(f s)
        freq_indices = (freq_indices[:, None] * 2 + torch.arange(2)).reshape(-1)
        self.register_buffer("freq_indices", freq_indices, persistent=False)
        self.register_buffer("num_bands_per_freq", freqs_per_band.sum(0), persistent=False)

        dim_inputs = tuple(2 * int(n) * self.channels for n in freqs_per_band.sum(1).tolist())
        self.band_split = BandSplit(dim, dim_inputs)
        self.mask_estimators = nn.ModuleList([MaskEstimator(dim, dim_inputs, mask_estimator_depth)])

    def forward(self, audio):
        batch, channels, length = audio.shape
        window = torch.hann_window(self.stft_kwargs["win_length"], device=audio.device)
        spec = torch.stft(audio.reshape(batch * channels, length).float(), **self.stft_kwargs, window=window,
                          return_complex=True)
        freqs, frames = spec.shape[-2:]
        # (b s) f t → b (f s) t
        spec = spec.reshape(batch, channels, freqs, frames).transpose(1, 2).reshape(batch, freqs * channels, frames)

        x = torch.view_as_real(spec[:, self.freq_indices])                 # b k t c
        x = self.band_split(x.permute(0, 2, 1, 3).reshape(batch, frames, -1))  # b t 频带 d
        for time_transformer, freq_transformer in self.layers:
            _, t, bands, d = x.shape
            x = time_transformer(x.transpose(1, 2).reshape(batch * bands, t, d)).reshape(batch, bands, t, d)
            x = freq_transformer(x.transpose(1, 2).reshape(batch * t, bands, d)).reshape(batch, t, bands, d)

        mask = self.mask_estimators[0](x).float().reshape(batch, frames, -1, 2).transpose(1, 2)
        mask = torch.view_as_complex(mask.contiguous())                     # b k t
        # 同一频点可能落在相邻的几个频带里，取各频带估计的平均
        index = self.freq_indices[None, :, None].expand(batch, -1, frames)
        summed = torch.zeros_like(spec).scatter_add_(1, index, mask)
        denom = self.num_bands_per_freq.repeat_interleave(channels)[:, None].clamp(min=1)
        spec = spec * (summed / denom)

        spec = spec.reshape(batch, freqs, channels, frames).transpose(1, 2).reshape(batch * channels, freqs, frames)
        spec[:, 0] = 0.0   # 去掉直流分量
        out = torch.istft(spec, **self.stft_kwargs, window=window, return_complex=False)
        return out.reshape(batch, channels, -1)
