"""Create installer only after the release gate accepts the exact local bundle."""
import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from subtitleflow import __version__
from subtitleflow.updates import platform_key
from tools.release_gate import gate, digest


def package(audit, review_build=False):
    bundle = ROOT / "dist" / ("SubtitleFlow.app" if sys.platform == "darwin" else "SubtitleFlow")
    gate(bundle, audit, inventory_only=review_build)
    out = ROOT / "dist/installers"
    out.mkdir(exist_ok=True)
    name = f"SubtitleFlow-{__version__}-{platform_key()}"
    if sys.platform == "darwin":
        installer = out / (name + ".dmg")
        stage = ROOT / "build/dmg-content"
        stage.mkdir(parents=True, exist_ok=True)
        app_copy = stage / "SubtitleFlow.app"
        shutil.copytree(bundle, app_copy, symlinks=True, dirs_exist_ok=True)
        applications = stage / "Applications"
        if not applications.exists():
            applications.symlink_to("/Applications", target_is_directory=True)
        subprocess.run(["hdiutil", "create", "-volname", "SubtitleFlow", "-srcfolder", str(stage),
                        "-ov", "-format", "UDZO", str(installer)], check=True)
    elif sys.platform == "win32":
        stage = ROOT / "build/installer"
        stage.mkdir(exist_ok=True)
        with zipfile.ZipFile(stage / "app.zip", "w", zipfile.ZIP_DEFLATED) as archive:
            for path in bundle.rglob("*"):
                if path.is_file():
                    archive.write(path, path.relative_to(bundle).as_posix())
            archive.write(ROOT / "packaging/uninstall.ps1", "uninstall.ps1")
            archive.writestr("installed-version.txt", __version__)
        shutil.copyfile(ROOT / "packaging/install.ps1", stage / "install.ps1")
        installer = out / (name + ".exe")
        sed = f"""[Version]
Class=IEXPRESS
SEDVersion=3
[Options]
PackagePurpose=InstallApp
ShowInstallProgramWindow=0
HideExtractAnimation=0
UseLongFileName=1
InsideCompressed=0
CAB_FixedSize=0
CAB_ResvCodeSigning=0
RebootMode=N
InstallPrompt=Install SubtitleFlow for the current user?
DisplayLicense=
FinishMessage=SubtitleFlow installed. Use the desktop shortcut to launch.
TargetName={installer}
FriendlyName=SubtitleFlow
AppLaunched=powershell.exe -NoProfile -ExecutionPolicy Bypass -File install.ps1
PostInstallCmd=<None>
AdminQuietInstCmd=
UserQuietInstCmd=
SourceFiles=SourceFiles
[SourceFiles]
SourceFiles0={stage}{os.sep}
[SourceFiles0]
%FILE0%=
%FILE1%=
[Strings]
FILE0="app.zip"
FILE1="install.ps1"
"""
        sed_path = stage / "installer.sed"
        sed_path.write_text(sed, encoding="ascii")
        subprocess.run(["iexpress.exe", "/N", "/Q", str(sed_path)], check=True)
        if not installer.exists():
            raise RuntimeError("IExpress did not create the installer")
    else:
        raise RuntimeError("Only Windows/macOS installers are supported")
    asset = {"file": installer.name, "sha256": digest(installer), "platform": platform_key(), "version": __version__}
    (out / (name + ".asset.json")).write_text(json.dumps(asset, indent=2), encoding="utf-8")
    print(installer)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit", type=Path, default=ROOT / "legal/release-audit.json")
    parser.add_argument("--review-build", action="store_true", help="Create an installer for testing, not a compliance-approved release")
    args = parser.parse_args()
    package(args.audit, args.review_build)
