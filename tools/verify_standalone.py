"""Run frozen app with development paths removed; also checks module origins."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

parser=argparse.ArgumentParser()
parser.add_argument("executable",type=Path)
args=parser.parse_args()
executable=args.executable.resolve()
env=os.environ.copy()
for name in list(env):
    if name.startswith(("PYTHON","CONDA","QT_","PYSIDE","VIRTUAL_ENV")):
        env.pop(name,None)
env["PATH"] = os.pathsep.join([os.path.join(env.get("SystemRoot","C:/Windows"), "System32")]) if sys.platform=="win32" else "/usr/bin:/bin:/usr/sbin:/sbin"
with tempfile.TemporaryDirectory(prefix="subtitleflow-independent-") as temp:
    output=Path(temp)/"result.json"
    completed=subprocess.run([str(executable),"--smoke-test",str(output)],cwd=temp,env=env,timeout=40,
                             capture_output=True,creationflags=subprocess.CREATE_NO_WINDOW if sys.platform=="win32" else 0)
    data=json.loads(output.read_text(encoding="utf-8")) if output.exists() else {}
    if completed.returncode or not data.get("ok") or not data.get("frozen"):
        raise RuntimeError(f"Standalone validation failed: {data}")
    destination=Path("build/standalone-validation.json")
    destination.parent.mkdir(exist_ok=True)
    destination.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
    print("Standalone application passed without Python/Conda/Qt development environment")
