from __future__ import annotations

import html
import ipaddress
import json
import os
import re
import socket
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

PROFILE_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
HTML_TAG_RE = re.compile(r"<[^>]{1,1000}>")
XHH_EMOJI_RE = re.compile(r"\[(?:表情|emoji|face)[:：]?[^\]]{0,80}\]", re.I)
LONG_RUN_RE = re.compile(r"(.)\1{19,}", re.S)
MULTI_BLANK_RE = re.compile(r"\n{3,}")
URL_RE = re.compile(r"https?://[^\s<>()]+", re.I)
SENSITIVE_KEY_RE = re.compile(
    r"(?:cookie|token|secret|authorization|pkey|device[_-]?id|"
    r"signing[_-]?key|^session$|^session[_-]?(?:id|key|token)$)",
    re.I,
)
BEARER_RE = re.compile(r"(?i)\b(Bearer|Token)\s+[A-Za-z0-9._~+/=-]{8,}")
COOKIE_RE = re.compile(r"(?i)\b(cookie|set-cookie)\s*[:=]\s*[^,\s;]+(?:;[^,\r\n]*)?")
SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(access[_-]?token|refresh[_-]?token|token|secret|device[_-]?id|"
    r"signing[_-]?key|session(?:_id)?|authorization)\b"
    r"(\s*[:=]\s*|%3[dD])([^&\s,;}\]]+)"
)


class SecurityError(ValueError):
    """Input failed a security boundary."""


def validate_profile_id(profile_id: str) -> str:
    value = str(profile_id).strip()
    if not PROFILE_RE.fullmatch(value):
        raise SecurityError("profile_id 仅允许 1-64 位字母、数字、下划线和短横线")
    return value


def redact_text(value: str) -> str:
    text = BEARER_RE.sub(r"\1 [REDACTED]", str(value))
    text = COOKIE_RE.sub(r"\1=[REDACTED]", text)
    return SECRET_ASSIGNMENT_RE.sub(r"\1\2[REDACTED]", text)


def redact_data(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): ("[REDACTED]" if SENSITIVE_KEY_RE.search(str(key)) else redact_data(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_data(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_data(item) for item in value)
    if isinstance(value, str):
        return redact_text(value)
    return value


def atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _chmod_best_effort(path.parent, 0o700)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        _chmod_best_effort(temp_path, 0o600)
        os.replace(temp_path, path)
        _chmod_best_effort(path, 0o600)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise


def _chmod_best_effort(path: Path, mode: int) -> None:
    if os.name != "posix":
        return
    try:
        path.chmod(mode)
    except OSError:
        return


def secure_unlink(path: Path) -> None:
    """Best-effort overwrite before deleting a small credential file."""

    try:
        size = path.stat().st_size
        with path.open("r+b", buffering=0) as handle:
            remaining = size
            zeroes = b"\0" * min(65536, max(1, size))
            while remaining > 0:
                chunk = zeroes[: min(len(zeroes), remaining)]
                handle.write(chunk)
                remaining -= len(chunk)
            handle.flush()
            os.fsync(handle.fileno())
    except FileNotFoundError:
        return
    finally:
        path.unlink(missing_ok=True)


def normalize_tracking_url(url: str) -> str:
    parsed = urlsplit(url)
    filtered = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith(("utm_", "spm", "track", "from"))
    ]
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(filtered), ""))


def clean_untrusted_text(
    value: str,
    *,
    bot_names: tuple[str, ...] = (),
    max_chars: int = 12000,
) -> str:
    text = html.unescape(str(value or ""))
    text = CONTROL_RE.sub("", text)
    text = HTML_TAG_RE.sub(" ", text)
    text = XHH_EMOJI_RE.sub(" ", text)
    for name in bot_names:
        normalized_name = name.strip().lstrip("@")
        if normalized_name:
            text = re.sub(
                rf"@{re.escape(normalized_name)}(?:\s|:|：)*",
                "",
                text,
                flags=re.I,
            )
    text = LONG_RUN_RE.sub(lambda match: match.group(1) * 8, text)
    text = URL_RE.sub(lambda match: normalize_tracking_url(match.group(0)), text)
    lines: list[str] = []
    previous = ""
    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = re.sub(r"[ \t]+", " ", raw_line).strip()
        if line and line != previous:
            lines.append(line)
            previous = line
        elif not line and lines and lines[-1] != "":
            lines.append("")
    cleaned = MULTI_BLANK_RE.sub("\n\n", "\n".join(lines)).strip()
    return cleaned[:max_chars]


def sanitize_reply_text(value: str, max_chars: int) -> str:
    text = redact_text(CONTROL_RE.sub("", str(value or "")))
    text = re.sub(
        r"\n?```(?:tool|json|debug|trace)[\s\S]*?```\n?",
        "\n",
        text,
        flags=re.I,
    )
    text = re.sub(r"(?im)^\s*(traceback|stack trace|tool call)\b.*$", "", text)
    text = MULTI_BLANK_RE.sub("\n\n", text).strip()
    if not text:
        raise SecurityError("模型生成了空回复")
    limit = max(1, int(max_chars))
    return text[:limit]


def validate_public_https_url(url: str) -> str:
    parsed = urlsplit(str(url).strip())
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise SecurityError("图片 URL 必须是公开 HTTPS 地址")
    if parsed.username or parsed.password:
        raise SecurityError("图片 URL 不允许包含认证信息")
    hostname = parsed.hostname.lower().rstrip(".")
    if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith(".local"):
        raise SecurityError("图片 URL 不允许访问本地主机")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))
    if not address.is_global:
        raise SecurityError("图片 URL 不允许访问内网或保留地址")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))


async def resolve_public_host(hostname: str) -> set[str]:
    loop = __import__("asyncio").get_running_loop()
    results = await loop.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    addresses = {str(item[4][0]) for item in results}
    if not addresses:
        raise SecurityError("图片域名无法解析")
    for value in addresses:
        if not ipaddress.ip_address(value).is_global:
            raise SecurityError("图片域名解析到内网或保留地址")
    return addresses
