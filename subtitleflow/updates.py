from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import platform
import re
import urllib.error
import urllib.parse
import urllib.request

from . import __version__

DEFAULT_REPO = "XMRayLabs/SubtitleFlow"


def version(value):
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", value)
    if not match:
        raise ValueError("更新版本必须为稳定版本 x.y.z")
    return tuple(map(int, match.groups()))


def platform_key():
    machine = platform.machine().lower()
    arch = "arm64" if machine in ("arm64", "aarch64") else "x86_64"
    return ("windows" if platform.system() == "Windows" else "macos") + "-" + arch


def allowed_url(url, repo):
    parsed = urllib.parse.urlsplit(url)
    return (parsed.scheme == "https" and parsed.hostname == "github.com"
            and parsed.path.startswith("/" + repo + "/releases/download/")
            and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment)


@dataclass(frozen=True)
class Release:
    version: str
    url: str
    sha256: str
    filename: str


def check(repo: str, current=__version__):
    if not repo:
        return None
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise ValueError("GitHub 仓库格式应为 owner/repository")
    request = urllib.request.Request(f"https://api.github.com/repos/{repo}/releases/latest",
                                     headers={"Accept": "application/vnd.github+json", "User-Agent": "SubtitleFlow"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            release = json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            exc.close()
            return None
        raise
    if release.get("draft") or release.get("prerelease") or version(release["tag_name"]) <= version(current):
        return None
    assets = release.get("assets", [])
    manifests = [a for a in assets if a["name"] == "update.json"]
    if len(manifests) != 1 or not allowed_url(manifests[0]["browser_download_url"], repo):
        raise ValueError("发布版本缺少有效 update.json")
    with urllib.request.urlopen(manifests[0]["browser_download_url"], timeout=20) as response:
        data = json.load(response)
    if version(data["version"]) != version(release["tag_name"]):
        raise ValueError("更新清单版本不一致")
    asset = data["platforms"].get(platform_key())
    if asset is None:
        raise ValueError("该版本尚无当前系统或架构的安装包")
    url, digest = asset["url"], asset["sha256"]
    if not allowed_url(url, repo) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
        raise ValueError("更新下载地址或校验值无效")
    filename = Path(urllib.parse.unquote(urllib.parse.urlsplit(url).path)).name
    suffix = ".exe" if platform.system() == "Windows" else ".dmg"
    if not filename.lower().endswith(suffix):
        raise ValueError("更新安装包类型与系统不符")
    return Release(data["version"], url, digest.lower(), filename)


def download(release: Release, directory: Path, cancel, progress=lambda n: None):
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / release.filename
    temporary = destination.with_suffix(destination.suffix + ".download")
    digest, size = hashlib.sha256(), 0
    try:
        with urllib.request.urlopen(release.url, timeout=30) as response, temporary.open("wb") as stream:
            while chunk := response.read(1024 * 256):
                if cancel.is_set():
                    raise RuntimeError("更新下载已取消")
                size += len(chunk)
                digest.update(chunk)
                stream.write(chunk)
                progress(size)
        if digest.hexdigest() != release.sha256:
            raise ValueError("安装包 SHA-256 校验失败")
        temporary.replace(destination)
        return destination
    finally:
        temporary.unlink(missing_ok=True)
