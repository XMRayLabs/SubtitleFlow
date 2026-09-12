"""Sign release manifests with the offline Ed25519 update key (see RELEASE.md / SECURITY.md)."""
import base64
import os
from pathlib import Path
import sys

from nacl.signing import SigningKey

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 当前发布密钥。轮换时只改这三个常量，subtitleflow/update_trust.py 由 provision_update_key.py 重新生成。
# 换 key_id 而不是沿用旧名：同一个 key_id 对应过两把公钥会让签名问题难以排查。见 SECURITY.md。
KEY_ID = "release-v2"
KEYRING_SERVICE = "SubtitleFlow-release"
KEYRING_ACCOUNT = "ed25519-v2"


def signing_key(use_local_key=False):
    """Return (key_id, seed hex) from the local keyring or CI environment, or (None, None) when unconfigured."""
    if use_local_key:
        from subtitleflow.gui import native_keyring
        seed = native_keyring().get_password(KEYRING_SERVICE, KEYRING_ACCOUNT)
        if not seed:
            raise ValueError("Local signing key unavailable")
        return KEY_ID, seed
    return os.environ.get("SUBTITLEFLOW_UPDATE_KEY_ID"), os.environ.get("SUBTITLEFLOW_UPDATE_SIGNING_KEY")


def sign(payload: bytes, key_id, seed):
    """Signed envelope {key_id, payload, signature}; the key must match the public key embedded in the app."""
    from subtitleflow.update_trust import TRUSTED_UPDATE_KEYS
    key = SigningKey(bytes.fromhex(seed))
    if TRUSTED_UPDATE_KEYS.get(key_id) != key.verify_key.encode().hex():
        raise ValueError("Signing key does not match embedded public key")
    return {"key_id": key_id, "payload": base64.b64encode(payload).decode(),
            "signature": base64.b64encode(key.sign(payload).signature).decode()}
