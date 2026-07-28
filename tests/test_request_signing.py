from __future__ import annotations

from xiaoheihe.request_signing import RequestSigner, generate_hkey


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
