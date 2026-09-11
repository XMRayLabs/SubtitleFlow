"""Sign release manifests with the offline Ed25519 update key (see RELEASE.md / SECURITY.md)."""
import base64
import os
from pathlib import Path
import sys

from nacl.signing import SigningKey

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def signing_key(use_local_key=False):
    """Return (key_id, seed hex) from the local keyring or CI environment, or (None, None) when unconfigured."""
    if use_local_key:
        from subtitleflow.gui import native_keyring
        seed = native_keyring().get_password("SubtitleFlow-release", "ed25519-v1")
        if not seed:
            raise ValueError("Local signing key unavailable")
        return "release-v1", seed
    return os.environ.get("SUBTITLEFLOW_UPDATE_KEY_ID"), os.environ.get("SUBTITLEFLOW_UPDATE_SIGNING_KEY")


def sign(payload: bytes, key_id, seed):
    """Signed envelope {key_id, payload, signature}; the key must match the public key embedded in the app."""
    from subtitleflow.update_trust import TRUSTED_UPDATE_KEYS
    key = SigningKey(bytes.fromhex(seed))
    if TRUSTED_UPDATE_KEYS.get(key_id) != key.verify_key.encode().hex():
        raise ValueError("Signing key does not match embedded public key")
    return {"key_id": key_id, "payload": base64.b64encode(payload).decode(),
            "signature": base64.b64encode(key.sign(payload).signature).decode()}
