from __future__ import annotations

import asyncio
import base64
import io
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

import qrcode

from .api_client import CredentialInvalidError, XiaoheiheApiClient
from .models import Credentials, LoginState, QRLoginSession
from .repository import Repository
from .security import (
    SecurityError,
    atomic_write_json,
    secure_unlink,
    validate_profile_id,
)
from .task_manager import TaskManager


class CredentialStore:
    def __init__(self, data_dir: Path) -> None:
        self.directory = data_dir / "credentials"
        self.directory.mkdir(parents=True, exist_ok=True)
        if __import__("os").name == "posix":
            try:
                self.directory.chmod(0o700)
            except OSError:
                return

    def path_for(self, profile_id: str) -> Path:
        safe_id = validate_profile_id(profile_id)
        path = (self.directory / f"{safe_id}.json").resolve(strict=False)
        if path.parent != self.directory.resolve(strict=False):
            raise SecurityError("凭证路径越界")
        return path

    def save(self, credentials: Credentials) -> None:
        atomic_write_json(self.path_for(credentials.profile_id), asdict(credentials))

    def load(self, profile_id: str) -> Credentials | None:
        path = self.path_for(profile_id)
        if not path.exists():
            return None
        with path.open(encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            raise ValueError("凭证文件格式无效")
        cookies = payload.get("cookies")
        if not isinstance(cookies, dict):
            raise ValueError("凭证 Cookie 格式无效")
        return Credentials(
            profile_id=validate_profile_id(str(payload.get("profile_id", profile_id))),
            uid=str(payload.get("uid", "")),
            nickname=str(payload.get("nickname", "")),
            cookies={str(key): str(value) for key, value in cookies.items()},
            access_token=str(payload.get("access_token", "")),
            refresh_token=str(payload.get("refresh_token", "")),
            device_id=str(payload.get("device_id", "")),
            signing_key=str(payload.get("signing_key", "")),
            logged_in_at=str(payload.get("logged_in_at", "")),
        )

    def delete(self, profile_id: str) -> None:
        secure_unlink(self.path_for(profile_id))

    def exists(self, profile_id: str) -> bool:
        return self.path_for(profile_id).exists()


class AuthService:
    def __init__(
        self,
        store: CredentialStore,
        repository: Repository,
        client_factory,
        task_manager: TaskManager | None = None,
        on_login: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        self.store = store
        self.repository = repository
        self._client_factory = client_factory
        self._tasks = task_manager
        self._on_login = on_login
        self._sessions: dict[str, QRLoginSession] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, profile_id: str) -> asyncio.Lock:
        return self._locks.setdefault(profile_id, asyncio.Lock())

    async def request_qr(self, profile_id: str) -> dict[str, Any]:
        profile_id = validate_profile_id(profile_id)
        async with self._lock(profile_id):
            account = await self.repository.account_state(profile_id)
            if account.get("status") in {
                LoginState.CREDENTIAL_INVALID.value,
                LoginState.FAILED.value,
            }:
                self.store.delete(profile_id)
            await self.repository.update_account_state(
                profile_id,
                status=LoginState.REQUESTING_QR.value,
                last_error="",
                consecutive_401=0,
                consecutive_403=0,
                consecutive_429=0,
                circuit_open_until=0,
            )
            client: XiaoheiheApiClient = await self._client_factory(profile_id, anonymous=True)
            try:
                session = await client.request_qr()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await self.repository.update_account_state(
                    profile_id,
                    status=LoginState.FAILED.value,
                    last_error=str(exc),
                )
                raise
            self._sessions[profile_id] = session
            await self.repository.update_account_state(
                profile_id, status=LoginState.WAITING_SCAN.value, last_error=""
            )
            result = session.public_dict(time.time())
            result["qr_image"] = _qr_data_url(session.qr_content)
            if self._tasks is not None:
                await self._tasks.start(
                    f"xhh-login-{profile_id}",
                    self._poll_login(profile_id),
                    replace=True,
                )
            return result

    async def _poll_login(self, profile_id: str) -> None:
        consecutive_failures = 0
        while True:
            await asyncio.sleep(3)
            try:
                result = await self.check(profile_id)
                consecutive_failures = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                consecutive_failures += 1
                session = self._sessions.get(profile_id)
                if session is None or time.time() >= session.expires_at:
                    return
                session.state = LoginState.WAITING_SCAN
                session.message = str(exc)[:300]
                await self.repository.update_account_state(
                    profile_id,
                    status=LoginState.WAITING_SCAN.value,
                    last_login_check_at=_iso_now(),
                    last_error=str(exc),
                )
                await asyncio.sleep(min(2**consecutive_failures, 10))
                continue
            state = str(result.get("state", ""))
            if state == LoginState.SUCCESS.value:
                if self._on_login is not None:
                    await self._on_login(profile_id)
                return
            if state in {
                LoginState.EXPIRED.value,
                LoginState.FAILED.value,
                LoginState.CREDENTIAL_INVALID.value,
                LoginState.LOGGED_OUT.value,
            }:
                return

    async def check(self, profile_id: str) -> dict[str, Any]:
        profile_id = validate_profile_id(profile_id)
        async with self._lock(profile_id):
            session = self._sessions.get(profile_id)
            if session is None:
                credentials = self.store.load(profile_id)
                if credentials is None:
                    return {
                        "profile_id": profile_id,
                        "state": LoginState.IDLE.value,
                        "has_credentials": False,
                        "message": "尚未生成二维码，或扫码登录尚未成功",
                    }
                client: XiaoheiheApiClient = await self._client_factory(profile_id)
                try:
                    await client.check_credentials()
                except CredentialInvalidError:
                    await self.repository.update_account_state(
                        profile_id,
                        status=LoginState.CREDENTIAL_INVALID.value,
                        last_login_check_at=_iso_now(),
                    )
                    return {
                        **credentials.public_dict(),
                        "state": LoginState.CREDENTIAL_INVALID.value,
                    }
                refreshed_credentials = getattr(client, "credentials", None)
                if refreshed_credentials is not None:
                    credentials = refreshed_credentials
                    self.store.save(credentials)
                await self.repository.update_account_state(
                    profile_id,
                    status=LoginState.SUCCESS.value,
                    last_login_check_at=_iso_now(),
                    last_error="",
                    consecutive_401=0,
                    consecutive_403=0,
                    consecutive_429=0,
                    circuit_open_until=0,
                )
                return {**credentials.public_dict(), "state": LoginState.SUCCESS.value}

            if time.time() >= session.expires_at:
                session.state = LoginState.EXPIRED
                await self.repository.update_account_state(
                    profile_id, status=LoginState.EXPIRED.value
                )
                return session.public_dict(time.time())

            client = await self._client_factory(profile_id, anonymous=True)
            state, message, credentials = await client.check_qr(session)
            session.state = state
            session.message = message
            result = session.public_dict(time.time())
            if credentials is not None:
                self.store.save(credentials)
                await self.repository.update_account_state(
                    profile_id,
                    status=LoginState.SUCCESS.value,
                    uid=credentials.uid,
                    nickname=credentials.nickname,
                    login_at=credentials.logged_in_at,
                    last_login_check_at=_iso_now(),
                    last_error="",
                    consecutive_401=0,
                    consecutive_403=0,
                    consecutive_429=0,
                    circuit_open_until=0,
                )
                result.update(credentials.public_dict())
                self._sessions.pop(profile_id, None)
            else:
                await self.repository.update_account_state(
                    profile_id,
                    status=state.value,
                    last_login_check_at=_iso_now(),
                    last_error=message if state is LoginState.FAILED else "",
                )
            return result

    async def status(self, profile_id: str) -> dict[str, Any]:
        profile_id = validate_profile_id(profile_id)
        session = self._sessions.get(profile_id)
        credentials = self.store.load(profile_id)
        account = await self.repository.account_state(profile_id)
        result = {
            "profile_id": profile_id,
            "state": account.get("status", LoginState.IDLE.value),
            "has_credentials": credentials is not None,
            "uid": credentials.uid if credentials else "",
            "nickname": credentials.nickname if credentials else "",
            "logged_in_at": credentials.logged_in_at if credentials else "",
            "last_login_check_at": account.get("last_login_check_at"),
        }
        if session is not None:
            result.update(session.public_dict(time.time()))
            result["qr_image"] = _qr_data_url(session.qr_content)
        return result

    async def logout(self, profile_id: str) -> dict[str, Any]:
        profile_id = validate_profile_id(profile_id)
        if self._tasks is not None:
            await self._tasks.cancel(f"xhh-login-{profile_id}")
        async with self._lock(profile_id):
            self._sessions.pop(profile_id, None)
            self.store.delete(profile_id)
            await self.repository.update_account_state(
                profile_id,
                status=LoginState.LOGGED_OUT.value,
                uid="",
                nickname="",
                login_at=None,
                last_error="",
                consecutive_401=0,
                consecutive_403=0,
                consecutive_429=0,
                circuit_open_until=0,
            )
        return {
            "profile_id": profile_id,
            "state": LoginState.LOGGED_OUT.value,
            "has_credentials": False,
        }


def _qr_data_url(content: str) -> str:
    image = qrcode.make(content)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _iso_now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()
