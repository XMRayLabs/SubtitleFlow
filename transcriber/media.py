"""用 ffmpeg 解码源文件并检测静音。音频和视频都可以，视频只取音轨。"""
import re
import subprocess

SAMPLE_RATE = 16000       # 模型要求 16k 单声道
SILENCE_NOISE_DB = -40.0
SILENCE_MIN_DURATION = 0.5

_SILENCE_START_RE = re.compile(r"silence_start:\s*(-?\d+(?:\.\d+)?)")
_SILENCE_END_RE = re.compile(r"silence_end:\s*(-?\d+(?:\.\d+)?)")
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _run(cmd):
    try:
        proc = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              creationflags=_NO_WINDOW)
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
        """返回 (音频采样, 时长秒, 静音区间列表)。"""
        import numpy as np
        decoded = _run([self.ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-i", path,
                        "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "f32le", "-"])
        audio = np.frombuffer(decoded.stdout, dtype=np.float32)
        if audio.size == 0:
            raise RuntimeError("源文件中没有可用的音频")
        return audio, audio.size / SAMPLE_RATE, self.silences(path)

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

    def clip(self, audio, segment):
        return audio[int(round(segment.start * SAMPLE_RATE)):int(round(segment.end * SAMPLE_RATE))]
