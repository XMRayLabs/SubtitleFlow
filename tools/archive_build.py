"""Archive the complete app, preserving macOS permissions and links."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from subtitleflow import __version__
from subtitleflow.updates import platform_key
out=ROOT/"dist/artifacts"
out.mkdir(parents=True,exist_ok=True)
name=f"SubtitleFlow-{__version__}-{platform_key()}"
if sys.platform=="darwin":
    subprocess.run(["ditto","-c","-k","--sequesterRsrc","--keepParent",str(ROOT/"dist/SubtitleFlow.app"),str(out/(name+".zip"))],check=True)
else:
    shutil.make_archive(str(out/name),"zip",ROOT/"dist","SubtitleFlow")
print(out)
