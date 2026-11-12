from __future__ import annotations

import base64
import hashlib

import pytest

from tests.helpers import decode_write_rnd, decrypt_write_rnd
from xiaoheihe.models import Credentials
from xiaoheihe.request_signing import (
    RequestSigner,
    build_write_rnd,
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


@pytest.mark.parametrize(
    ("pathname", "expected"),
    [
        (
            "/bbs/app/comment/create",
            "16:AAECAwQFBgcICQoLfkeZnJBDf8gtT6mv9hPslIwrekJzKpip3WoO7J-NyPi6TamAEDR6bw4jWpVVayUz"
            "jOqyFv336KZW5MZy_f87u-vhOt0JMQUmBhRRU6Wdai4dt561cHVIpVghnYQ",
        ),
        (
            "/测试/评论/",
            "16:AAECAwQFBgcICQoLfkeZnJBDf8gtT6mv9hPslIwrekJzKpip3WoO7J-NyPi6TamAEDR6bw4jWpVVayUz"
            "jOqykioPL2izuwG-Fnr4ZKq3SI39iX4-bShjI_DKPWsGgOA",
        ),
    ],
)
def test_write_rnd_matches_node_crypto_vector(monkeypatch, pathname: str, expected: str) -> None:
    # Independently generated with Node crypto.createCipheriv('aes-256-gcm'),
    # JSON.stringify and IV bytes 00..0b. Only the test fixes the random input.
    def fixed_iv(length: int) -> bytes:
        assert length == 12
        return bytes(range(length))

    monkeypatch.setattr("xiaoheihe.request_signing.secrets.token_bytes", fixed_iv)
    assert build_write_rnd("0123456789ABCDEF0123456789ABCDEF", "1700000000", pathname) == expected


@pytest.mark.parametrize(
    "pathname", ["", "/", "/bbs/app/comment/create", "/bbs/app/comment/create/"]
)
def test_write_rnd_round_trip(pathname: str) -> None:
    rnd = build_write_rnd("0123456789ABCDEF0123456789ABCDEF", "1700000000", pathname)
    assert rnd.startswith("16:")
    assert "=" not in rnd
    assert decrypt_write_rnd(rnd) == [
        "1700000000",
        "0123456789ABCDEF0123456789ABCDEF",
        pathname if pathname.endswith("/") else f"{pathname}/",
    ]


def test_write_rnd_uses_fresh_iv_for_identical_inputs() -> None:
    first = build_write_rnd("fixed", "100", "/bbs/app/comment/create")
    second = build_write_rnd("fixed", "100", "/bbs/app/comment/create")
    assert decode_write_rnd(first)[0] != decode_write_rnd(second)[0]
    assert first != second
    assert (
        decrypt_write_rnd(first)
        == decrypt_write_rnd(second)
        == ["100", "fixed", "/bbs/app/comment/create/"]
    )


def test_write_rnd_defaults_to_root_path() -> None:
    # Callers that do not know the endpoint path keep a valid signature whose
    # pathname segment degrades to "/" (matches the old two-argument call site).
    assert decrypt_write_rnd(build_write_rnd("fixed", "100")) == ["100", "fixed", "/"]
