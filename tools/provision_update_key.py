"""Provision an Ed25519 key in OS credentials; commit only the public key."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from subtitleflow.gui import native_keyring
from nacl.signing import SigningKey

store = native_keyring()
seed = store.get_password("SubtitleFlow-release", "ed25519-v1")
if seed is None:
    key = SigningKey.generate()
    seed = key.encode().hex()
    store.set_password("SubtitleFlow-release", "ed25519-v1", seed)
key = SigningKey(bytes.fromhex(seed))
public = key.verify_key.encode().hex()
Path("subtitleflow/update_trust.py").write_text("TRUSTED_UPDATE_KEYS = " + repr({"release-v1": public}) + "\n", encoding="utf-8")
print("Update public key provisioned; private key retained in OS credentials")
