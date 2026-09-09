import argparse
import json
from pathlib import Path
import re

parser = argparse.ArgumentParser()
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
    if asset["version"] != args.version:
        raise ValueError("Asset version mismatch")
    if asset["platform"] in platforms:
        raise ValueError("Duplicate platform asset")
    platforms[asset["platform"]] = {
        "url": f"https://github.com/{args.repo}/releases/download/v{args.version}/{asset['file']}",
        "sha256": asset["sha256"]}
if not platforms:
    raise ValueError("No installer metadata found")
args.output.write_text(json.dumps({"version": args.version, "platforms": platforms}, indent=2), encoding="utf-8")
