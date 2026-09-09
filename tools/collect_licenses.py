"""Collect exact installed license files and source archives for release review."""
import argparse
import hashlib
from importlib import metadata
import json
from pathlib import Path
import shutil
import sys
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
LEGAL = ROOT / "legal"
COMPONENTS = {
    "PySide6-Essentials": ("6.11.2", "LGPL-3.0-only"),
    "shiboken6": ("6.11.2", "LGPL-3.0-only"),
    "keyring": ("25.6.0", "MIT"),
    "jaraco.classes": ("3.4.0", "MIT"),
    "jaraco.context": ("6.1.2", "MIT"),
    "jaraco.functools": ("4.6.0", "MIT"),
    "more-itertools": ("11.1.0", "MIT"),
    "pyinstaller": ("6.19.0", "GPL-2.0-or-later WITH Bootloader-exception"),
    "pyinstaller-hooks-contrib": ("2026.7", "GPL-2.0-or-later AND Apache-2.0"),
    "altgraph": ("0.17.5", "MIT"),
    "packaging": ("26.3", "Apache-2.0 OR BSD-2-Clause"),
    "setuptools": ("84.0.0", "MIT"),
}
if sys.platform == "win32":
    COMPONENTS.update({"pywin32-ctypes": ("0.2.3", "BSD-3-Clause"), "pefile": ("2024.8.26", "MIT")})
elif sys.platform == "darwin":
    COMPONENTS["macholib"] = ("1.16.4", "MIT")

SOURCES = {
    "qtbase-everywhere-src-6.11.2.tar.xz": "https://download.qt.io/official_releases/qt/6.11/6.11.2/submodules/qtbase-everywhere-src-6.11.2.tar.xz",
    "pyside-setup-everywhere-src-6.11.2.tar.xz": "https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.11.2-src/pyside-setup-everywhere-src-6.11.2.tar.xz",
}
PYVER = ".".join(map(str, sys.version_info[:3]))
SOURCES[f"Python-{PYVER}.tar.xz"] = f"https://www.python.org/ftp/python/{PYVER}/Python-{PYVER}.tar.xz"


def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def collect(fetch=False):
    licenses = LEGAL / "licenses"
    licenses.mkdir(parents=True, exist_ok=True)
    components = []
    for name, (expected, selected) in COMPONENTS.items():
        dist = metadata.distribution(name)
        if dist.version != expected:
            raise RuntimeError(f"{name}: expected {expected}, found {dist.version}; re-review before changing versions")
        copied = []
        for entry in dist.files or []:
            parts = Path(str(entry)).parts
            if any(p.lower().startswith(("license", "copying", "copyright", "authors", "notice")) for p in parts) and not str(entry).endswith((".py", ".pyc")):
                source = Path(dist.locate_file(entry))
                if source.is_file():
                    dest = licenses / name / str(entry).replace("../", "_")
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, dest)
                    copied.append(str(dest.relative_to(LEGAL)))
        if not copied:
            raise RuntimeError(f"{name}: no license text supplied")
        components.append({"name": name, "version": dist.version, "selected_license": selected,
                           "upstream": f"https://pypi.org/project/{name}/{dist.version}/",
                           "license_files": copied})
    runtime_license = Path(sys.base_prefix) / "LICENSE.txt"
    if not runtime_license.exists():
        runtime_license = Path(sys.base_prefix) / "LICENSE"
    if runtime_license.exists():
        shutil.copyfile(runtime_license, licenses / "PYTHON-RUNTIME-LICENSE.txt")
    records = []
    sources = LEGAL / "sources"
    sources.mkdir(exist_ok=True)
    for name, url in SOURCES.items():
        archive = sources / name
        if fetch and not archive.exists():
            print(f"Downloading {name}", flush=True)
            part = archive.with_suffix(".partial")
            with urllib.request.urlopen(url, timeout=120) as response, part.open("wb") as stream:
                shutil.copyfileobj(response, stream)
            part.replace(archive)
        if archive.exists():
            # Extract text license notices only, never execute or extract arbitrary source paths.
            with tarfile.open(archive) as tar:
                for member in tar:
                    filename = Path(member.name).name.lower()
                    if member.isfile() and member.size < 2_000_000 and filename.startswith(("license", "copying", "copyright", "notice", "authors")):
                        parts = Path(member.name).parts
                        if ".." in parts or Path(member.name).is_absolute():
                            raise RuntimeError("Unsafe archive path")
                        dest = licenses / "upstream-source" / member.name
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        with tar.extractfile(member) as stream:
                            dest.write_bytes(stream.read())
            records.append({"file": name, "url": url, "sha256": sha(archive)})
    data = {"python": PYVER, "platform": sys.platform, "components": components,
            "sources": records, "review_status": "pending-binary-review",
            "required_sources": list(SOURCES)}
    (LEGAL / "component-manifest.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Collected {len(components)} components, {len(records)}/{len(SOURCES)} source archives", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--fetch-sources", action="store_true")
    collect(parser.parse_args().fetch_sources)
