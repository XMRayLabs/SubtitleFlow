import argparse
import hashlib
import base64
import os
from nacl.signing import SigningKey
import json
from pathlib import Path
import re

parser = argparse.ArgumentParser()
parser.add_argument("--use-local-key", action="store_true")
parser.add_argument("--repo", required=True)
parser.add_argument("--version", required=True)
parser.add_argument("--assets", type=Path, required=True)
parser.add_argument("--output", type=Path, default=Path("update.json"))
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
seed = os.environ.get("SUBTITLEFLOW_UPDATE_SIGNING_KEY")
key_id = os.environ.get("SUBTITLEFLOW_UPDATE_KEY_ID")
if args.use_local_key:
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from subtitleflow.gui import native_keyring
    seed = native_keyring().get_password("SubtitleFlow-release", "ed25519-v1")
    key_id = "release-v1"
    if not seed:
        raise ValueError("Local signing key unavailable")
if not seed or not key_id:
    # Keep installer drafts usable, but never publish an unsigned update.json.
    args.output.unlink(missing_ok=True)
    print("Signing key not configured; update.json intentionally omitted")
else:
    key = SigningKey(bytes.fromhex(seed))
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from subtitleflow.update_trust import TRUSTED_UPDATE_KEYS
    if TRUSTED_UPDATE_KEYS.get(key_id) != key.verify_key.encode().hex():
        raise ValueError("Signing key does not match embedded public key")
    envelope = {"key_id": key_id, "payload": base64.b64encode(payload).decode(),
                "signature": base64.b64encode(key.sign(payload).signature).decode()}
    # Compatibility for pre-signature clients; new clients trust only the verified payload.
    envelope.update(json.loads(payload))
    args.output.write_text(json.dumps(envelope, indent=2), encoding="utf-8")
