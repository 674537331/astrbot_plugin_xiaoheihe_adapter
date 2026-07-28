from __future__ import annotations

from xiaoheihe.request_signing import RequestSigner


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
    assert len(first.headers["X-XHH-Signature"]) == 64
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
