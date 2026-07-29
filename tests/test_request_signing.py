from __future__ import annotations

import base64
import hashlib

from xiaoheihe.models import Credentials
from xiaoheihe.request_signing import (
    RequestSigner,
    ensure_client_identity,
    generate_hkey,
    generate_xhh_token_id,
)


def test_signing_is_canonical_and_secret_optional() -> None:
    signer = RequestSigner()
    first = signer.sign(
        "POST",
        "/path",
        params={"b": 2, "a": 1},
        json_body={"z": 3},
        signing_key="mock-key",
        device_id="mock-device",
        now=100,
        nonce="fixed",
    )
    second = signer.sign(
        "POST",
        "/path",
        params={"a": 1, "b": 2},
        json_body={"z": 3},
        signing_key="mock-key",
        device_id="mock-device",
        now=100,
        nonce="fixed",
    )
    assert first == second
    assert first.headers == {}
    assert len(first.params["hkey"]) == 7
    unsigned = signer.sign(
        "GET",
        "/login",
        params=None,
        json_body=None,
        signing_key="",
        device_id="",
        now=100,
        nonce="fixed",
    )
    assert unsigned.headers == {}
    assert unsigned.params["hkey"]


def test_hkey_matches_mit_reference_vector() -> None:
    assert (
        generate_hkey(
            "/bbs/app/user/message",
            1_700_000_000,
            "0123456789ABCDEF0123456789ABCDEF",
        )
        == "YT27P47"
    )


def test_xhh_token_id_matches_mit_web_cookie_shape() -> None:
    parts = ("a" * 32, "b" * 32, "c" * 32)
    token = generate_xhh_token_id(now=1_700_000_000, random_parts=parts)
    decoded = base64.b64decode(token)
    assert len(decoded) == 65
    assert decoded[-1:] == b"\x00"
    assert (
        decoded[:16]
        == hashlib.md5(
            b"1700000000",
            usedforsecurity=False,
        ).digest()
    )


def test_missing_client_identity_is_generated_once() -> None:
    credentials = Credentials("default", "1", "Bot", {"pkey": "fixture"})
    assert ensure_client_identity(credentials, device_id="a" * 32) is True
    first_token = credentials.cookies["x_xhh_tokenid"]
    assert credentials.device_id == "a" * 32
    assert ensure_client_identity(credentials, device_id="b" * 32) is False
    assert credentials.cookies["x_xhh_tokenid"] == first_token
