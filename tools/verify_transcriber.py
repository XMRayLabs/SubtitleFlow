"""Check that a built transcription service starts on its own and answers an authenticated request.

    python tools/verify_transcriber.py dist/SubtitleFlow-Transcriber/SubtitleFlow-Transcriber.exe
"""
import json
from pathlib import Path
import subprocess
import sys
import threading
import urllib.request


def main(exe):
    process = subprocess.Popen([str(exe), "--engine", "fake"], stdout=subprocess.PIPE, text=True)
    try:
        hello = {}
        reader = threading.Thread(target=lambda: hello.update(json.loads(process.stdout.readline())), daemon=True)
        reader.start()
        reader.join(120)
        if "token" not in hello:
            raise SystemExit("service did not announce port/token")
        request = urllib.request.Request(f"http://127.0.0.1:{hello['port']}/v1/health",
                                         headers={"Authorization": f"Bearer {hello['token']}"})
        with urllib.request.urlopen(request, timeout=10) as response:
            health = json.loads(response.read())
        print(json.dumps({"hello_api_version": hello["api_version"], "health": health}))
        if health.get("api_version") != hello["api_version"]:
            raise SystemExit("health check returned a different API version")
    finally:
        process.kill()
        process.wait(10)


if __name__ == "__main__":
    main(Path(sys.argv[1]))
