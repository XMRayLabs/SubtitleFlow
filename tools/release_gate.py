"""Fail closed until exact binaries, notices, source archives and reviewer evidence agree."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            value.update(chunk)
    return value.hexdigest()


def inventory(bundle):
    return [{"path": p.relative_to(bundle).as_posix(), "sha256": digest(p)}
            for p in sorted(bundle.rglob("*")) if p.is_file()
            and "legal" not in p.relative_to(bundle).parts]


def gate(bundle, audit_path, inventory_only=False):
    manifest = json.loads((ROOT / "legal/component-manifest.json").read_text(encoding="utf-8"))
    files = inventory(bundle)
    if not files:
        raise RuntimeError("Empty application bundle")
    output = ROOT / "build/binary-inventory.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(files, indent=2), encoding="utf-8")
    if inventory_only:
        print(output)
        return
    for name in manifest["required_sources"]:
        matches = [s for s in manifest["sources"] if s["file"] == name]
        path = ROOT / "legal/sources" / name
        if len(matches) != 1 or not path.exists() or digest(path) != matches[0]["sha256"]:
            raise RuntimeError(f"Missing/mismatched corresponding source: {name}")
    # Qt DLL allowlist is supplementary; the complete actual bundle requires reviewer evidence.
    allowed_qt = {"Qt6Core.dll", "Qt6Gui.dll", "Qt6Widgets.dll"}
    for item in files:
        name = Path(item["path"]).name
        if name.startswith("Qt6") and name.endswith(".dll") and name not in allowed_qt:
            raise RuntimeError(f"Unaudited Qt module: {name}")
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    if audit.get("platform") != sys.platform or not audit.get("reviewer"):
        raise RuntimeError("Platform-specific reviewer identity required")
    if audit.get("inventory_sha256") != digest(output):
        raise RuntimeError("Review does not match actual application files")
    for field in ("licenses_compatible", "all_native_dependencies_reviewed", "corresponding_sources_complete",
                  "library_replacement_verified", "notices_complete", "platform_smoke_passed"):
        if audit.get(field) is not True:
            raise RuntimeError(f"Release review not complete: {field}")
    # Verify actual bundled legal files match working release material.
    legal_dirs = [p for p in bundle.rglob("legal") if p.is_dir()]
    if not legal_dirs:
        raise RuntimeError("Bundle missing legal folder")
    for source in (ROOT / "legal").rglob("*"):
        if source.is_file() and source.name != "release-audit.json" and not any((folder / source.relative_to(ROOT / "legal")).is_file()
                                      and digest(folder / source.relative_to(ROOT / "legal")) == digest(source)
                                      for folder in legal_dirs):
            raise RuntimeError(f"Bundle missing/stale notice or source: {source.name}")
    print("Release gate passed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--audit", type=Path, default=ROOT / "legal/release-audit.json")
    parser.add_argument("--inventory-only", action="store_true")
    args = parser.parse_args()
    gate(args.bundle, args.audit, args.inventory_only)
