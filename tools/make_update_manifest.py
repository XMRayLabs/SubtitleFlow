import argparse
import hashlib
import json
from pathlib import Path
import re

from signing import sign, signing_key

parser = argparse.ArgumentParser()
parser.add_argument("--use-local-key", action="store_true")
parser.add_argument("--repo", required=True)
parser.add_argument("--version", required=True)
parser.add_argument("--assets", type=Path, required=True)
parser.add_argument("--output", type=Path, default=Path("update.json"))
parser.add_argument("--require-key", action="store_true",
                    help="fail instead of omitting update.json when no signing key is configured")
args = parser.parse_args()
if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repo):
    parser.error("repo must be owner/repository")
if not re.fullmatch(r"\d+\.\d+\.\d+", args.version):
    parser.error("version must be x.y.z")
platforms = {}
for path in args.assets.glob("*.asset.json"):
    asset = json.loads(path.read_text(encoding="utf-8"))
    filename = asset["file"]
    if Path(filename).name != filename or "\\" in filename or ":" in filename:
        raise ValueError("Unsafe installer filename")
    installer = args.assets / filename
    with installer.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != asset["sha256"]:
        raise ValueError("Installer digest does not match metadata")
    if asset["version"] != args.version:
        raise ValueError("Asset version mismatch")
    if asset["platform"] in platforms:
        raise ValueError("Duplicate platform asset")
    platforms[asset["platform"]] = {
        "url": f"https://github.com/{args.repo}/releases/download/v{args.version}/{asset['file']}",
        "sha256": asset["sha256"]}
if not platforms:
    raise ValueError("No installer metadata found")
payload = json.dumps({"version": args.version, "platforms": platforms}, separators=(",", ":")).encode()
key_id, seed = signing_key(args.use_local_key)
if not seed or not key_id:
    if args.require_key:
        raise SystemExit("Signing key not configured")
    # Keep installer drafts usable, but never publish an unsigned update.json.
    args.output.unlink(missing_ok=True)
    print("Signing key not configured; update.json intentionally omitted")
else:
    envelope = sign(payload, key_id, seed)
    # Compatibility for pre-signature clients; new clients trust only the verified payload.
    envelope.update(json.loads(payload))
    args.output.write_text(json.dumps(envelope, indent=2), encoding="utf-8")
