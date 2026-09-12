"""下载、校验、安装与卸载转录服务（见 ADR-0001）。

服务包分卷放在固定标签的 GitHub Release 上，transcriber.json 用与软件更新相同的离线密钥签名；
模型从 HuggingFace 按固定 revision 下载，失败时换镜像，每个文件都按清单中的哈希校验。
installed.json 最后写入：中途中断的安装永远不会被当成已安装。
"""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import urllib.error
import urllib.request
import zipfile

from .safety import atomic_write, bounded_response, read_json
from .updates import open_update, verify_manifest

# 当前软件版本配套的转录服务发布标签；换用新服务时随软件更新一起修改
TRANSCRIBER_TAG = "transcriber-v1.0.1"
MODEL_ENDPOINTS = ["https://huggingface.co", "https://hf-mirror.com"]
KIND = "subtitleflow-transcriber"
PLATFORM = "windows-x86_64"
CHUNK = 1024 * 1024
TIMEOUT = 30
_HEX64 = re.compile(r"[0-9a-f]{64}")
_PART = re.compile(r"[A-Za-z0-9._-]+\.\d{3}")


class Cancelled(Exception):
    pass


@dataclass(frozen=True)
class TranscriberRelease:
    version: str
    api_version: int
    parts: tuple
    archive_sha256: str
    archive_size: int
    entry: str
    model_repo: str
    model_revision: str
    model_files: dict

    @property
    def total_size(self):
        return self.archive_size + sum(f["size"] for f in self.model_files.values())


@dataclass(frozen=True)
class InstalledService:
    version: str
    api_version: int
    exe: Path
    model_dir: Path

    def command(self):
        return [str(self.exe), "--model", str(self.model_dir)]


def _safe_relative(value) -> str:
    """清单里的相对路径不能跳出安装目录。"""
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise ValueError("转录服务清单包含不安全的路径")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise ValueError("转录服务清单包含不安全的路径")
    return value


def _digest(value):
    if not isinstance(value, str) or not _HEX64.fullmatch(value):
        raise ValueError("转录服务清单校验值无效")
    return value


def _size(value):
    if not isinstance(value, int) or value < 0:
        raise ValueError("转录服务清单文件大小无效")
    return value


def parse_manifest(data: dict) -> TranscriberRelease:
    if data.get("kind") != KIND or data.get("platform") != PLATFORM:
        raise ValueError("转录服务清单与当前系统不匹配")
    if not re.fullmatch(r"\d+\.\d+\.\d+", str(data.get("version"))) or not isinstance(data.get("api_version"), int):
        raise ValueError("转录服务清单版本无效")
    parts = []
    for part in data.get("parts") or []:
        if not isinstance(part.get("name"), str) or not _PART.fullmatch(part["name"]):
            raise ValueError("转录服务清单包含不安全的分卷名")
        parts.append({"name": part["name"], "size": _size(part.get("size")), "sha256": _digest(part.get("sha256"))})
    model = data.get("model") or {}
    if not re.fullmatch(r"[0-9a-f]{40}", str(model.get("revision"))):
        raise ValueError("模型版本必须是完整的提交哈希")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", str(model.get("repo"))):
        raise ValueError("模型仓库名无效")
    files = {_safe_relative(name): {"size": _size(info.get("size")), "sha256": _digest(info.get("sha256"))}
             for name, info in (model.get("files") or {}).items()}
    if not parts or not files:
        raise ValueError("转录服务清单不完整")
    return TranscriberRelease(data["version"], data["api_version"], tuple(parts), _digest(data.get("archive_sha256")),
                              _size(data.get("archive_size")), _safe_relative(data.get("entry")),
                              model["repo"], model["revision"], files)


def fetch_release(repo, tag=TRANSCRIBER_TAG, opener=open_update) -> TranscriberRelease:
    url = f"https://github.com/{repo}/releases/download/{tag}/transcriber.json"
    try:
        with opener(url, timeout=TIMEOUT) as response:
            raw = bounded_response(response, 4 * 1024 * 1024)
    except urllib.error.HTTPError as exc:
        raise ValueError(f"无法获取转录服务清单（HTTP {exc.code}）")
    return parse_manifest(verify_manifest(raw))


def default_root() -> Path:
    local = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    return Path(local) / "SubtitleFlow"


def nvidia_available() -> bool:
    """装有 NVIDIA 驱动（提供 CUDA 驱动库 nvcuda.dll）即视为可用。"""
    if sys.platform != "win32":
        return False
    import ctypes
    try:
        ctypes.WinDLL("nvcuda.dll")
        return True
    except OSError:
        return False


def support_problem():
    """返回不支持的原因；支持时返回 None。"""
    if sys.platform != "win32":
        return "当前系统暂不支持音频转录（目前仅支持 Windows）"
    if not nvidia_available():
        return "当前设备暂不支持音频转录（需要 NVIDIA 显卡）"
    return None


def _layout(root: Path):
    base = root / "transcriber"
    return base, base / "installed.json"


def installed_service(root: Path) -> InstalledService | None:
    base, record = _layout(root)
    try:
        data = read_json(record)
        service = InstalledService(data["version"], data["api_version"], base / _safe_relative(data["exe"]),
                                   base / _safe_relative(data["model_dir"]))
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return service if service.exe.is_file() and service.model_dir.is_dir() else None


def uninstall(root: Path) -> int:
    """删除服务与模型，返回释放的字节数。"""
    base, _ = _layout(root)
    if not base.exists():
        return 0
    freed = sum(p.stat().st_size for p in base.rglob("*") if p.is_file())
    shutil.rmtree(base)
    return freed


class _Parts:
    """把分卷按顺序拼成一个只读、可随机访问的文件，供 zipfile 直接读取，不必先合并到磁盘。"""

    def __init__(self, paths):
        self.files = [p.open("rb") for p in paths]
        self.sizes = [p.stat().st_size for p in paths]
        self.total, self.pos = sum(self.sizes), 0

    def seekable(self):
        return True

    def readable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, offset, whence=0):
        self.pos = {0: offset, 1: self.pos + offset, 2: self.total + offset}[whence]
        return self.pos

    def read(self, n=-1):
        n = self.total - self.pos if n is None or n < 0 else min(n, self.total - self.pos)
        out, start = bytearray(), 0
        for handle, size in zip(self.files, self.sizes):
            if n <= 0:
                break
            if self.pos < start + size:
                handle.seek(self.pos - start)
                chunk = handle.read(min(n, start + size - self.pos))
                out += chunk
                self.pos += len(chunk)
                n -= len(chunk)
            start += size
        return bytes(out)

    def close(self):
        for handle in self.files:
            handle.close()


class Installer:
    """progress(已下载字节, 总字节, 当前步骤说明)"""

    def __init__(self, release: TranscriberRelease, root: Path, cancel, progress=lambda done, total, label: None,
                 repo=None, parts_base=None, model_endpoints=MODEL_ENDPOINTS, opener=open_update, chunk_size=CHUNK):
        self.release, self.root, self.cancel, self.progress = release, Path(root), cancel, progress
        self.parts_base = parts_base or f"https://github.com/{repo}/releases/download/{TRANSCRIBER_TAG}"
        self.model_endpoints, self.opener, self.chunk_size = list(model_endpoints), opener, chunk_size
        self.done = 0

    def run(self) -> InstalledService:
        base, record = _layout(self.root)
        downloads = base / "downloads" / self.release.version
        model_dir = base / "models" / self.release.model_revision
        service_dir = base / "service" / self.release.version
        parts = []
        for part in self.release.parts:
            parts.append(self._fetch([f"{self.parts_base}/{part['name']}"], downloads / part["name"], part, "下载转录服务"))
        self._check_archive(parts)
        for name, info in self.release.model_files.items():
            urls = [f"{e}/{self.release.model_repo}/resolve/{self.release.model_revision}/{name}" for e in self.model_endpoints]
            self._fetch(urls, model_dir / name, info, "下载模型")
        self._extract(parts, service_dir)
        exe = service_dir / self.release.entry
        if not exe.is_file():
            raise ValueError("转录服务包缺少可执行文件")
        atomic_write(record, json.dumps({
            "version": self.release.version, "api_version": self.release.api_version,
            "exe": exe.relative_to(base).as_posix(), "model_dir": model_dir.relative_to(base).as_posix(),
        }, ensure_ascii=False).encode("utf-8"))
        shutil.rmtree(downloads, ignore_errors=True)
        # 更新后删除旧版本的服务和旧 revision 的模型；模型未变化时已被校验复用
        for keep in (service_dir, model_dir):
            for old in keep.parent.iterdir():
                if old != keep:
                    shutil.rmtree(old, ignore_errors=True)
        return installed_service(self.root)

    def _report(self, label):
        self.progress(self.done, self.release.total_size, label)

    def _fetch(self, urls, target: Path, info, label) -> Path:
        """断点续传下载到 target，校验大小与 SHA-256；多个地址依次尝试。"""
        if target.is_file() and target.stat().st_size == info["size"] and _file_sha(target) == info["sha256"]:
            self.done += info["size"]
            self._report(label)
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(target.name + ".partial")
        start_done, error = self.done, None
        for url in urls:
            try:
                self._download(url, partial, info["size"], label, start_done)
                break
            except (urllib.error.URLError, OSError) as exc:
                error = exc
                self.done = start_done
        else:
            raise OSError(f"下载失败：{error}")
        if _file_sha(partial) != info["sha256"]:
            partial.unlink(missing_ok=True)
            raise ValueError(f"{target.name} 校验失败，文件可能被篡改，请重新安装")
        partial.replace(target)
        return target

    def _download(self, url, partial: Path, size, label, start_done):
        have = partial.stat().st_size if partial.exists() else 0
        if have > size:
            partial.unlink()
            have = 0
        if have == size:
            self.done = start_done + size
            return
        request = urllib.request.Request(url, headers={"User-Agent": "SubtitleFlow"})
        if have:
            request.add_header("Range", f"bytes={have}-")
        with self.opener(request, timeout=TIMEOUT) as response:
            if have and getattr(response, "status", 200) != 206:
                have = 0   # 服务器不支持续传，从头下载
            with partial.open("ab" if have else "wb") as stream:
                self.done = start_done + have
                while chunk := response.read(self.chunk_size):
                    stream.write(chunk)
                    self.done += len(chunk)
                    if self.done - start_done > size:
                        raise ValueError("下载内容超过清单中的大小")
                    self._report(label)
                    if self.cancel.is_set():
                        raise Cancelled("安装已取消，下次会从断点继续")

    def _check_archive(self, parts):
        whole = hashlib.sha256()
        for part in parts:
            with part.open("rb") as stream:
                while chunk := stream.read(CHUNK):
                    whole.update(chunk)
        if whole.hexdigest() != self.release.archive_sha256:
            raise ValueError("转录服务包校验失败，请重新安装")

    def _extract(self, parts, service_dir: Path):
        staging = service_dir.with_name(service_dir.name + ".partial")
        shutil.rmtree(staging, ignore_errors=True)
        source = _Parts(parts)
        try:
            with zipfile.ZipFile(source) as bundle:
                for member in bundle.infolist():
                    target = staging / _safe_relative(member.filename.rstrip("/"))
                    if member.is_dir():
                        target.mkdir(parents=True, exist_ok=True)
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with bundle.open(member) as src, target.open("wb") as dst:
                        shutil.copyfileobj(src, dst, CHUNK)
                    if self.cancel.is_set():
                        raise Cancelled("安装已取消")
        finally:
            source.close()
        shutil.rmtree(service_dir, ignore_errors=True)
        staging.replace(service_dir)


def _file_sha(path: Path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()
