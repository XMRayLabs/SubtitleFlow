"""Query OSV for installed packages. Network errors and findings fail the build."""
import importlib.metadata as metadata
import json
from pathlib import Path
import re
import urllib.request

packages = {re.sub(r"[-_.]+", "-", d.metadata["Name"]).lower(): d.version for d in metadata.distributions()}
queries = [{"package": {"name": name, "ecosystem": "PyPI"}, "version": version}
           for name, version in sorted(packages.items()) if name != "subtitleflow"]
request = urllib.request.Request("https://api.osv.dev/v1/querybatch",
    data=json.dumps({"queries": queries}).encode(), headers={"Content-Type": "application/json"})
with urllib.request.urlopen(request, timeout=60) as response:
    results = json.load(response)["results"]
if len(results) != len(queries):
    raise RuntimeError("Incomplete vulnerability response")
output = Path("build/dependency-audit.json")
output.parent.mkdir(exist_ok=True)
output.write_text(json.dumps({"queries": queries, "results": results}, indent=2), encoding="utf-8")
findings = [(q["package"]["name"], r["vulns"]) for q, r in zip(queries, results) if r.get("vulns")]
if findings:
    raise RuntimeError("Known dependency vulnerabilities: " + repr(findings))
print(f"OSV: {len(queries)} packages, no matching known vulnerabilities")
