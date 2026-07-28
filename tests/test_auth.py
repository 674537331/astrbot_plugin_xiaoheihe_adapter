from __future__ import annotations

import time

from xiaoheihe.api_client import CredentialInvalidError
from xiaoheihe.auth import AuthService, CredentialStore
from xiaoheihe.models import Credentials, LoginState, QRLoginSession
from xiaoheihe.task_manager import TaskManager


def mock_credentials(profile_id="default") -> Credentials:
    return Credentials(
        profile_id=profile_id,
        uid="10001",
        nickname="MockUser",
        cookies={"mock_sid": "redacted-fixture-value"},
        logged_in_at="2026-07-28T00:00:00+00:00",
    )


def test_credential_atomic_save_load_delete(tmp_path) -> None:
    store = CredentialStore(tmp_path)
    store.save(mock_credentials())
    path = store.path_for("default")
    assert path.parent.name == "credentials"
    assert store.load("default").uid == "10001"
    store.delete("default")
    assert not path.exists()


class FakeLoginClient:
    def __init__(self, state=LoginState.SUCCESS) -> None:
        self.state = state

    async def request_qr(self):
        return QRLoginSession(
            profile_id="default",
            request_id="qr-1",
            qr_content="https://example.com/qr?mock=1",
            expires_at=time.time() + 180,
        )

    async def check_qr(self, session):
        return self.state, "state", mock_credentials() if self.state is LoginState.SUCCESS else None

    async def check_credentials(self):
        return {"uid": "10001"}


async def test_auth_qr_success_persists_credentials(tmp_path, repository) -> None:
    store = CredentialStore(tmp_path)
    client = FakeLoginClient()

    async def factory(profile_id, anonymous=False):
        return client

    auth = AuthService(store, repository, factory)
    await repository.update_account_state(
        "default",
        consecutive_401=3,
        consecutive_403=2,
        consecutive_429=1,
        circuit_open_until=time.time() + 600,
    )
    qr = await auth.request_qr("default")
    assert qr["state"] == "waiting_scan"
    assert qr["qr_image"].startswith("data:image/png;base64,")
    result = await auth.check("default")
    assert result["state"] == "success"
    assert store.load("default").nickname == "MockUser"
    state = await repository.account_state("default")
    assert state["consecutive_401"] == 0
    assert state["circuit_open_until"] == 0


async def test_auth_qr_expired(tmp_path, repository) -> None:
    store = CredentialStore(tmp_path)

    async def factory(profile_id, anonymous=False):
        return FakeLoginClient()

    auth = AuthService(store, repository, factory)
    auth._sessions["default"] = QRLoginSession(
        profile_id="default",
        request_id="expired",
        qr_content="https://example.com/qr",
        expires_at=time.time() - 1,
    )
    result = await auth.check("default")
    assert result["state"] == "expired"


async def test_logout_removes_only_selected_profile(tmp_path, repository) -> None:
    store = CredentialStore(tmp_path)
    store.save(mock_credentials("default"))
    store.save(mock_credentials("other"))

    async def factory(profile_id, anonymous=False):
        return FakeLoginClient()

    auth = AuthService(store, repository, factory)
    await auth.logout("default")
    assert store.load("default") is None
    assert store.load("other") is not None


async def test_qr_background_poll_is_owned_and_cancelled(tmp_path, repository) -> None:
    store = CredentialStore(tmp_path)
    tasks = TaskManager()
    client = FakeLoginClient(LoginState.WAITING_SCAN)

    async def factory(profile_id, anonymous=False):
        return client

    auth = AuthService(store, repository, factory, task_manager=tasks)
    await auth.request_qr("default")
    assert "xhh-login-default" in tasks.task_names()
    await auth.logout("default")
    assert "xhh-login-default" not in tasks.task_names()
    await tasks.close()


async def test_saved_credentials_are_checked_and_invalidated(tmp_path, repository) -> None:
    store = CredentialStore(tmp_path)
    store.save(mock_credentials())
    client = FakeLoginClient()

    async def factory(profile_id, anonymous=False):
        return client

    auth = AuthService(store, repository, factory)
    assert (await auth.check("default"))["state"] == LoginState.SUCCESS.value

    async def invalid_check():
        raise CredentialInvalidError("expired", category="credential_invalid")

    client.check_credentials = invalid_check
    result = await auth.check("default")
    assert result["state"] == LoginState.CREDENTIAL_INVALID.value


async def test_background_login_poll_notifies_runtime(tmp_path, repository, monkeypatch) -> None:
    store = CredentialStore(tmp_path)
    client = FakeLoginClient()
    notified = []

    async def factory(profile_id, anonymous=False):
        return client

    async def on_login(profile_id):
        notified.append(profile_id)

    async def no_sleep(seconds):
        return None

    monkeypatch.setattr("xiaoheihe.auth.asyncio.sleep", no_sleep)
    auth = AuthService(store, repository, factory, on_login=on_login)
    await auth.request_qr("default")
    status = await auth.status("default")
    assert status["qr_image"].startswith("data:image/png;base64,")
    await auth._poll_login("default")
    assert notified == ["default"]
