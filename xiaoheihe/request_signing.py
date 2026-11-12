from __future__ import annotations

import base64
import hashlib
import json
import hmac
import secrets
import time
from dataclasses import dataclass
from typing import Any

from .models import Credentials

# The hkey algorithm is independently ported to Python from the MIT-licensed
# heybox-core implementation by XiaHouSheng. See THIRD_PARTY_NOTICES.md.
_HKEY_ALPHABET = "AB45STUVWZEFGJ6CH01D237IXYPQRKLMN89"  # gitleaks:allow
# MD5 is required by the upstream compatibility contract. It is not used for
# password storage, integrity protection, or any local security decision.


@dataclass(frozen=True, slots=True)
class SignedRequest:
    params: dict[str, str]
    headers: dict[str, str]


class RequestSigner:
    """Build Xiaoheihe's per-request hkey, timestamp and nonce query fields."""

    def sign(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None,
        json_body: dict[str, Any] | None,
        signing_key: str,
        device_id: str,
        now: int | None = None,
        nonce: str | None = None,
    ) -> SignedRequest:
        timestamp = now if now is not None else int(time.time())
        request_nonce = (
            nonce
            or hashlib.md5(
                f"{timestamp}{secrets.token_hex(16)}".encode(),
                usedforsecurity=False,
            )
            .hexdigest()
            .upper()
        )
        normalized = {
            str(key): str(value)
            for key, value in sorted((params or {}).items())
            if value is not None
        }
        normalized.setdefault("_time", str(timestamp))
        normalized.setdefault("nonce", request_nonce)
        normalized.setdefault("hkey", generate_hkey(path, timestamp, request_nonce))
        if device_id:
            normalized.setdefault("device_id", device_id)
        # This signer produces the common hkey. Endpoint-specific web write
        # parameters and `_rnd` are added by the API client over this nonce/time.
        del method, json_body, signing_key
        return SignedRequest(params=normalized, headers={})


def generate_device_id() -> str:
    """Return the stable 32-character web client identifier expected upstream."""

    return secrets.token_hex(16)


def generate_xhh_token_id(
    *,
    now: int | None = None,
    random_parts: tuple[str, str, str] | None = None,
) -> str:
    """Build the non-secret web client cookie used by Xiaoheihe requests.

    The shape is independently implemented from the MIT-licensed heybox-bot
    client. Random input is used instead of copying constants from unlicensed
    reference projects.
    """

    timestamp = str(now if now is not None else int(time.time()))
    parts = random_parts or tuple(secrets.token_hex(16) for _ in range(3))
    raw = b"".join(
        hashlib.md5(part.encode(), usedforsecurity=False).digest() for part in (timestamp, *parts)
    )
    return base64.b64encode(raw + b"\x00").decode("ascii")


def ensure_client_identity(
    credentials: Credentials,
    *,
    device_id: str = "",
) -> bool:
    """Fill missing web-client metadata and report whether credentials changed."""

    changed = False
    if not credentials.device_id:
        credentials.device_id = device_id or generate_device_id()
        changed = True
    if not credentials.cookies.get("x_xhh_tokenid"):
        credentials.cookies["x_xhh_tokenid"] = generate_xhh_token_id()
        changed = True
    return changed


# The web client sends an extra `_rnd` signature on write requests. The server
# answers `缺失参数` when it is missing, even when `hkey`, `_time` and `nonce`
# are all valid, so a write is never accepted without it. Both constants are
# public values shipped in the www.xiaoheihe.cn bundle; they are not secrets of
# this project and rotate when the upstream client changes.
# [rnd16][2026-11-12] 网页端写入签名升级到 v16：AES-GCM(32B key, 12B IV, AAD=heybox-rnd:v16)
# 明文 = JSON.stringify([time, nonce, pathname+"/"])，输出 "16:" + base64url(iv+ct+tag)。
# 旧 v15 HMAC 摘要已被服务端拒收（表现为"评论功能暂不可用"）。已用真实浏览器
# 抓包的 _rnd 回解密验证算法一致。密钥为 www.xiaoheihe.cn 前端 bundle 公开常量。
_WRITE_RND16_KEY_B64 = "HFFSTsqmvG3uGxPNXDQW+1VARfqgG4nT6LS1PX7F8NQ="  # gitleaks:allow
_WRITE_RND16_AAD = b"heybox-rnd:v16"


def build_write_rnd(nonce: str, timestamp: str, pathname: str = "") -> str:
    """Return the `_rnd` write signature in `16:<base64url(iv||ct||tag)>` form."""

    import base64 as _b64
    import os as _os

    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key = _b64.b64decode(_WRITE_RND16_KEY_B64)
    iv = _os.urandom(12)
    path = pathname if pathname.endswith("/") else f"{pathname}/"
    plaintext = json.dumps([str(timestamp), str(nonce), path], separators=(",", ":")).encode()
    ciphertext = AESGCM(key).encrypt(iv, plaintext, _WRITE_RND16_AAD)
    raw = iv + ciphertext
    encoded = _b64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    return f"16:{encoded}"


def generate_hkey(path: str, timestamp: int, nonce: str) -> str:
    normalized_path = f"/{'/'.join(part for part in str(path).split('/') if part)}/"
    parts = (
        _map_to_alphabet(str(timestamp), _HKEY_ALPHABET[:-2]),
        _map_to_alphabet(normalized_path, _HKEY_ALPHABET),
        _map_to_alphabet(str(nonce), _HKEY_ALPHABET),
    )
    interleaved = "".join(
        part[index]
        for index in range(max(len(part) for part in parts))
        for part in parts
        if index < len(part)
    )[:20]
    digest = hashlib.md5(interleaved.encode(), usedforsecurity=False).hexdigest()
    mixed = _mix_tail([ord(char) for char in digest[-6:]])
    suffix = str(sum(mixed) % 100).zfill(2)
    prefix = _map_to_alphabet(digest[:5], _HKEY_ALPHABET[:-4])
    return f"{prefix}{suffix}"


def _map_to_alphabet(value: str, alphabet: str) -> str:
    return "".join(alphabet[ord(char) % len(alphabet)] for char in value)


def _xtime(value: int) -> int:
    return (255 & ((value << 1) ^ 27)) if value & 128 else value << 1


def _mul3(value: int) -> int:
    return _xtime(value) ^ value


def _mul4(value: int) -> int:
    return _mul3(_xtime(value))


def _mul8(value: int) -> int:
    return _mul4(_mul3(_xtime(value)))


def _mul14(value: int) -> int:
    return _mul8(value) ^ _mul4(value) ^ _mul3(value)


def _mix_tail(values: list[int]) -> list[int]:
    first, second, third, fourth = values[:4]
    return [
        _mul14(first) ^ _mul8(second) ^ _mul4(third) ^ _mul3(fourth),
        _mul3(first) ^ _mul14(second) ^ _mul8(third) ^ _mul4(fourth),
        _mul4(first) ^ _mul3(second) ^ _mul14(third) ^ _mul8(fourth),
        _mul8(first) ^ _mul4(second) ^ _mul3(third) ^ _mul14(fourth),
        *values[4:],
    ]
