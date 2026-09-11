"""Package the PyInstaller-built transcription service for GitHub Releases (see ADR-0001).

GitHub limits each release asset to 2 GiB, while the CUDA build compresses to 2–3 GB, so the zip is
split into numbered parts. transcriber.json is signed with the same offline key as update.json and
lists every part, the whole archive, and each model file (the model's remote code runs with
trust_remote_code, so it must be pinned by hash as well as by revision).

    python tools/transcriber_release.py --version 1.0.0 --dist dist/SubtitleFlow-Transcriber \
        --model-dir <snapshot dir> --output dist/transcriber-release [--use-local-key]
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from signing import sign, signing_key  # noqa: E402
from transcriber import API_VERSION  # noqa: E402

PART_SIZE = 1900 * 1024 * 1024   # safely below GitHub's 2 GiB asset limit
MODEL_REPO = "OpenMOSS-Team/MOSS-Transcribe-Diarize"
PLATFORM = "windows-x86_64"
KIND = "subtitleflow-transcriber"

__all__ = ["archive", "split", "payload", "sign", "main"]


def file_digest(path: Path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def archive(dist: Path, target: Path) -> Path:
    """Zip the onedir build, keeping its top-level folder name."""
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, allowZip64=True, compresslevel=6) as bundle:
        for path in sorted(dist.rglob("*")):
            if path.is_file():
                bundle.write(path, Path(dist.name) / path.relative_to(dist))
    return target


def split(source: Path, out_dir: Path, part_size=PART_SIZE) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    parts = []
    with source.open("rb") as stream:
        while chunk := stream.read(part_size):
            part = out_dir / f"{source.name}.{len(parts) + 1:03d}"
            part.write_bytes(chunk)
            parts.append(part)
    return parts


def model_files(model_dir: Path):
    """Every model file relative to model_dir, skipping the .cache folder snapshot_download(local_dir=...) creates."""
    files = {}
    for path in sorted(model_dir.rglob("*")):
        relative = path.relative_to(model_dir)
        if path.is_file() and ".cache" not in relative.parts:
            files[relative.as_posix()] = {"size": path.stat().st_size, "sha256": file_digest(path)}
    return files


def payload(version, parts, entry, model_repo, model_revision, model_dir) -> bytes:
    whole = hashlib.sha256()
    for part in parts:
        with part.open("rb") as stream:
            while chunk := stream.read(1 << 20):
                whole.update(chunk)
    data = {
        "kind": KIND, "version": version, "api_version": API_VERSION, "platform": PLATFORM,
        "parts": [{"name": p.name, "size": p.stat().st_size, "sha256": file_digest(p)} for p in parts],
        "archive_sha256": whole.hexdigest(), "archive_size": sum(p.stat().st_size for p in parts),
        "entry": entry,
        "model": {"repo": model_repo, "revision": model_revision, "files": model_files(Path(model_dir))},
    }
    return json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Package the transcription service release")
    parser.add_argument("--version", required=True)
    parser.add_argument("--dist", type=Path, required=True, help="PyInstaller onedir output folder")
    parser.add_argument("--model-dir", type=Path, required=True, help="model snapshot folder at the pinned revision")
    parser.add_argument("--model-revision", required=True, help="full 40-character commit hash of the model")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--part-size", type=int, default=PART_SIZE)
    parser.add_argument("--use-local-key", action="store_true")
    args = parser.parse_args(argv)
    if len(args.model_revision) != 40:
        parser.error("--model-revision must be the full commit hash")
    args.output.mkdir(parents=True, exist_ok=True)
    name = f"SubtitleFlow-Transcriber-{args.version}-{PLATFORM}.zip"
    bundle = archive(args.dist, args.output / name)
    parts = split(bundle, args.output, args.part_size)
    bundle.unlink()
    exe = next(p for p in args.dist.iterdir() if p.suffix.lower() == ".exe")
    data = payload(args.version, parts, f"{args.dist.name}/{exe.name}", MODEL_REPO, args.model_revision, args.model_dir)
    key_id, seed = signing_key(args.use_local_key)
    manifest = args.output / "transcriber.json"
    if not seed or not key_id:
        # Parts stay usable for testing, but an unsigned manifest is never produced.
        manifest.unlink(missing_ok=True)
        print("Signing key not configured; transcriber.json intentionally omitted")
    else:
        manifest.write_text(json.dumps(sign(data, key_id, seed), indent=2), encoding="utf-8")
    print("\n".join(f"{p.name}  {p.stat().st_size}" for p in parts))


if __name__ == "__main__":
    main()
