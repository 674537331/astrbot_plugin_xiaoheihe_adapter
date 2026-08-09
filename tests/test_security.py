from __future__ import annotations

import pytest

from xiaoheihe.security import (
    SecurityError,
    redact_data,
    redact_log_text,
    redact_text,
    sanitize_reply_text,
    validate_public_https_url,
)


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/a.png",
        "file:///etc/passwd",
        "https://127.0.0.1/a.png",
        "https://10.0.0.1/a.png",
        "https://user:pw@example.com/a.png",
    ],
)
def test_ssrf_and_local_url_protection(url: str) -> None:
    with pytest.raises(SecurityError):
        validate_public_https_url(url)


def test_public_https_is_allowed() -> None:
    assert validate_public_https_url("https://cdn.example.com/a.png") == (
        "https://cdn.example.com/a.png"
    )


def test_sensitive_log_redaction() -> None:
    assert "secret-value" not in redact_text("Authorization: Bearer secret-value")
    assert "device-123" not in redact_text(
        "https://example.test/path?device_id=device-123&token=token-456"
    )
    diagnostic = redact_log_text(
        "failed https://user:password@example.test/image.png?signature=private#fragment"
    )
    assert "user:password" not in diagnostic
    assert "signature=private" not in diagnostic
    assert "fragment" not in diagnostic
    assert "https://example.test/image.png?[REDACTED]#[REDACTED]" in diagnostic
    assert redact_data({"cookie": "secret", "nested": {"token": "secret"}}) == {
        "cookie": "[REDACTED]",
        "nested": {"token": "[REDACTED]"},
    }


def test_reply_cleanup_and_length() -> None:
    result = sanitize_reply_text("hello\n```tool\nsecret\n```\nworld", 8)
    assert result == "hello\nwo"
    with pytest.raises(SecurityError, match="空回复"):
        sanitize_reply_text("```debug\nx\n```", 100)
