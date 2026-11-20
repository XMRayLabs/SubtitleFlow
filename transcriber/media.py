"""用 ffmpeg 解码源文件并检测静音。音频和视频都可以，视频只取音轨。"""
from dataclasses import dataclass
import re
import subprocess

from . import enhance as enhancement

SAMPLE_RATE = 16000       # 模型要求 16k 单声道
SILENCE_NOISE_DB = -40.0
SILENCE_MIN_DURATION = 0.5

_SILENCE_START_RE = re.compile(r"silence_start:\s*(-?\d+(?:\.\d+)?)")
_SILENCE_END_RE = re.compile(r"silence_end:\s*(-?\d+(?:\.\d+)?)")
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass
class Source:
    path: str
    audio: object     # 16k 单声道采样（numpy 数组）


def _run(cmd, data=None):
    try:
        proc = subprocess.run(cmd, input=data, stdin=None if data is not None else subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=_NO_WINDOW)
    except FileNotFoundError:
        raise RuntimeError("找不到 ffmpeg")
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.decode("utf-8", "replace").strip().splitlines()[-3:])
        raise RuntimeError(f"ffmpeg 无法读取源文件：{tail}")
    return proc


class FfmpegMedia:
    def __init__(self, ffmpeg="ffmpeg"):
        self.ffmpeg = ffmpeg

    def probe(self, path):
        """返回 (Source, 时长秒, 静音区间列表)。"""
        import numpy as np
        decoded = _run([self.ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-i", path,
                        "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "f32le", "-"])
        audio = np.frombuffer(decoded.stdout, dtype=np.float32)
        if audio.size == 0:
            raise RuntimeError("源文件中没有可用的音频")
        return Source(path, audio), audio.size / SAMPLE_RATE, self.silences(path)

    def silences(self, path):
        proc = _run([self.ffmpeg, "-nostdin", "-hide_banner", "-i", path, "-vn",
                     "-af", f"silencedetect=noise={SILENCE_NOISE_DB}dB:d={SILENCE_MIN_DURATION}", "-f", "null", "-"])
        pending, intervals = [], []
        for line in proc.stderr.decode("utf-8", "replace").splitlines():
            m = _SILENCE_START_RE.search(line)
            if m:
                pending.append(float(m.group(1)))
                continue
            m = _SILENCE_END_RE.search(line)
            if m and pending:
                intervals.append((pending.pop(0), float(m.group(1))))
        return intervals

    def clip(self, source, segment, enhance=None):
        """取出一个转录分段的 16k 单声道采样。给出 enhance 时，按增强模型的要求重新解码成
        44.1k 立体声，增强后再由 ffmpeg 转回 16k 单声道。只解码这一段，内存不随源文件变长而增加。"""
        start, end = int(round(segment.start * SAMPLE_RATE)), int(round(segment.end * SAMPLE_RATE))
        plain = source.audio[start:end]
        if enhance is None:
            return plain
        import numpy as np
        rate, channels = enhancement.SAMPLE_RATE, enhancement.CHANNELS
        decoded = _run([self.ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error",
                        "-ss", f"{segment.start:.3f}", "-t", f"{segment.end - segment.start:.3f}", "-i", source.path,
                        "-vn", "-ac", str(channels), "-ar", str(rate), "-f", "f32le", "-"])
        hifi = np.frombuffer(decoded.stdout, dtype=np.float32).reshape(-1, channels)
        if hifi.size == 0:
            return plain
        enhanced = np.ascontiguousarray(enhance(hifi), dtype=np.float32)
        resampled = _run([self.ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error",
                          "-f", "f32le", "-ar", str(rate), "-ac", str(channels), "-i", "-",
                          "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "f32le", "-"], enhanced.tobytes())
        audio = np.frombuffer(resampled.stdout, dtype=np.float32)
        # 重新解码与重采样可能差几个采样；对齐到原分段长度，时间戳才与不增强时一致
        return np.pad(audio, (0, max(0, plain.size - audio.size)))[:plain.size]
