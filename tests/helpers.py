from __future__ import annotations

import base64
import json
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def decode_write_rnd(rnd: str) -> tuple[bytes, bytes]:
    """Split the wire-format v16 signature into IV and ciphertext with tag."""

    version, payload = rnd.split(":", 1)
    assert version == "16"
    assert "=" not in payload
    raw = base64.b64decode(payload + "=" * (-len(payload) % 4), altchars=b"-_", validate=True)
    assert len(raw) >= 12 + 16
    return raw[:12], raw[12:]


def decrypt_write_rnd(rnd: str) -> list[str]:
    """Check the protocol independently of the production signer's constants."""

    iv, ciphertext = decode_write_rnd(rnd)
    # Public browser constant from issue #35, not an account credential.
    key = base64.b64decode("HFFSTsqmvG3uGxPNXDQW+1VARfqgG4nT6LS1PX7F8NQ=")  # gitleaks:allow
    return json.loads(AESGCM(key).decrypt(iv, ciphertext, b"heybox-rnd:v16"))


def load_fixture(name: str):
    path = Path(__file__).parent / "fixtures" / name
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)
