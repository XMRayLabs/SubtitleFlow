"""CycloneDX package SBOM plus binding to actual native binary inventory."""
import hashlib
import importlib.metadata as metadata
import json
from pathlib import Path
import platform
import ssl
import sys

inventory = Path("build/binary-inventory.json")
components = [{"type": "library", "name": d.metadata["Name"], "version": d.version,
               "purl": f"pkg:pypi/{d.metadata['Name'].lower() .replace('_','-')}@{d.version}"}
              for d in metadata.distributions()]
components += [{"type": "library", "name": "CPython", "version": platform.python_version()},
               {"type": "library", "name": "Python OpenSSL", "version": ssl.OPENSSL_VERSION}]
from PySide6.QtCore import qVersion
components.append({"type": "library", "name": "Qt", "version": qVersion()})
from cryptography.hazmat.backends.openssl.backend import backend
components.append({"type": "library", "name": "cryptography OpenSSL", "version": backend.openssl_version_text()})
data = {"bomFormat": "CycloneDX", "specVersion": "1.5", "version": 1,
        "metadata": {"component": {"type": "application", "name": "SubtitleFlow"},
          "properties": [{"name": "subtitleflow:binary-inventory-sha256", "value": hashlib.sha256(inventory.read_bytes()).hexdigest()},
                         {"name": "subtitleflow:platform", "value": sys.platform},
                         {"name": "subtitleflow:coverage", "value": "Python packages and major runtimes; native transitive inventory requires release review"}]},
        "components": components}
Path("build/sbom.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
