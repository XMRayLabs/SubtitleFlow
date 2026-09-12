"""Package the PyInstaller-built transcription service for GitHub Releases (see ADR-0001).

GitHub limits each release asset to 2 GiB, while the CUDA build compresses to 2–3 GB, so the zip is
split into numbered parts. transcriber.json is signed with the same offline key as update.json and
lists every part, the whole archive, and each model file (the model's remote code runs with
trust_remote_code, so it must be pinned by hash as well as by revision).

    python tools/transcriber_release.py --version 1.0.0 --dist dist/SubtitleFlow-Transcriber \
        --model-dir <snapshot dir> --output dist/transcriber-release [--use-local-key]

Without a key the payload is kept as transcriber-payload.json instead of being discarded, so the
release can be packaged on the build machine and signed later where the key lives. The release
workflow does this in a separate job behind the approved `release` environment, re-hashing the parts
first; the same command works locally as a fallback:

    python tools/transcriber_release.py --sign-payload transcriber-payload.json \
        --parts-dir <folder with the .zip.NNN parts> --output release-assets --use-local-key
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
PAYLOAD_NAME = "transcriber-payload.json"   # unsigned manifest body, kept for offline signing
MODEL_REPO = "OpenMOSS-Team/MOSS-Transcribe-Diarize"
PLATFORM = "windows-x86_64"
KIND = "subtitleflow-transcriber"

__all__ = ["archive", "split", "payload", "sign", "verify_parts", "write_manifest", "main"]


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


def verify_parts(data: bytes, parts_dir: Path):
    """Re-hash every part the payload lists before a signature vouches for them.

    The payload and the parts travel together from the build job, so this cannot catch a build that lied
    about both; it catches a part that was truncated, swapped or re-uploaded after the hashes were taken.
    """
    manifest = json.loads(data)
    whole, total = hashlib.sha256(), 0
    for part in manifest["parts"]:
        name = part["name"]
        path = parts_dir / name
        if Path(name).name != name or not path.is_file():
            raise ValueError(f"Part listed in payload is missing: {name}")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(1 << 20):
                digest.update(chunk)
                whole.update(chunk)
        size = path.stat().st_size
        if size != part["size"] or digest.hexdigest() != part["sha256"]:
            raise ValueError(f"Part does not match payload: {name}")
        total += size
    if total != manifest["archive_size"] or whole.hexdigest() != manifest["archive_sha256"]:
        raise ValueError("Parts do not reassemble into the archive the payload describes")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Package the transcription service release")
    parser.add_argument("--version")
    parser.add_argument("--dist", type=Path, help="PyInstaller onedir output folder")
    parser.add_argument("--model-dir", type=Path, help="model snapshot folder at the pinned revision")
    parser.add_argument("--model-revision", help="full 40-character commit hash of the model")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--part-size", type=int, default=PART_SIZE)
    parser.add_argument("--use-local-key", action="store_true")
    parser.add_argument("--sign-payload", type=Path,
                        help=f"sign an existing {PAYLOAD_NAME} from the build machine; needs no build or model")
    parser.add_argument("--parts-dir", type=Path,
                        help="with --sign-payload: re-hash the parts in this folder against the payload before signing")
    parser.add_argument("--require-key", action="store_true",
                        help="fail instead of keeping an unsigned payload when no signing key is configured")
    args = parser.parse_args(argv)
    if args.parts_dir and not args.sign_payload:
        parser.error("--parts-dir only applies to --sign-payload")
    if not args.sign_payload:
        missing = [name for name in ("version", "dist", "model_dir", "model_revision") if getattr(args, name) is None]
        if missing:
            parser.error("--" + ", --".join(name.replace("_", "-") for name in missing) + " required when packaging")
        if len(args.model_revision) != 40:
            parser.error("--model-revision must be the full commit hash")
    args.output.mkdir(parents=True, exist_ok=True)
    if args.sign_payload:
        data = args.sign_payload.read_bytes()
        if args.parts_dir:
            verify_parts(data, args.parts_dir)
        return write_manifest(data, args.output, args.use_local_key, args.require_key)
    name = f"SubtitleFlow-Transcriber-{args.version}-{PLATFORM}.zip"
    bundle = archive(args.dist, args.output / name)
    parts = split(bundle, args.output, args.part_size)
    bundle.unlink()
    exe = next(p for p in args.dist.iterdir() if p.suffix.lower() == ".exe")
    data = payload(args.version, parts, f"{args.dist.name}/{exe.name}", MODEL_REPO, args.model_revision, args.model_dir)
    write_manifest(data, args.output, args.use_local_key, args.require_key)
    print("\n".join(f"{p.name}  {p.stat().st_size}" for p in parts))


def write_manifest(data: bytes, output: Path, use_local_key: bool, require_key=False):
    """Sign the payload into transcriber.json, or keep it unsigned for offline signing.

    The hashes can only be computed where the parts and the model live, which is the build machine.
    Discarding them when no key is configured would force whoever holds the key to reproduce a
    byte-identical build, so the unsigned payload is kept for `--sign-payload` instead.
    """
    output.mkdir(parents=True, exist_ok=True)
    key_id, seed = signing_key(use_local_key)
    manifest, unsigned = output / "transcriber.json", output / PAYLOAD_NAME
    if not seed or not key_id:
        if require_key:
            # The signing job exists only to sign; quietly handing back a payload would leave a draft nobody can install
            raise ValueError("Signing key not configured")
        # An unsigned manifest is never produced; the payload alone is inert without a signature.
        manifest.unlink(missing_ok=True)
        unsigned.write_bytes(data)
        print(f"Signing key not configured; wrote {PAYLOAD_NAME} to sign offline with --sign-payload")
        return
    manifest.write_text(json.dumps(sign(data, key_id, seed), indent=2), encoding="utf-8")
    unsigned.unlink(missing_ok=True)
    print(f"Signed transcriber.json with {key_id}")


if __name__ == "__main__":
    main()
