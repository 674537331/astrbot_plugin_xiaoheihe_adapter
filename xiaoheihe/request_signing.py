from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode


@dataclass(frozen=True, slots=True)
class SignedRequest:
    params: dict[str, str]
    headers: dict[str, str]


class RequestSigner:
    """Deterministic canonical signer without copied platform constants.

    Xiaoheihe's private signing contract is not documented. This implementation
    signs authenticated requests only when a credential-derived signing key is
    available. It remains replaceable at the boundary when real fixtures prove
    a different contract.
    """

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
        timestamp = now or int(time.time())
        request_nonce = nonce or secrets.token_hex(8)
        normalized = {
            str(key): str(value)
            for key, value in sorted((params or {}).items())
            if value is not None
        }
        normalized.setdefault("_time", str(timestamp))
        normalized.setdefault("nonce", request_nonce)
        if device_id:
            normalized.setdefault("device_id", device_id)
        body = json.dumps(
            json_body or {}, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        )
        canonical = "\n".join(
            [
                method.upper(),
                path,
                urlencode(sorted(normalized.items())),
                hashlib.sha256(body.encode("utf-8")).hexdigest(),
            ]
        )
        headers: dict[str, str] = {}
        if signing_key:
            headers["X-XHH-Signature"] = hmac.new(
                signing_key.encode("utf-8"),
                canonical.encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
        return SignedRequest(params=normalized, headers=headers)
