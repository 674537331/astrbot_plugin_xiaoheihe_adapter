from __future__ import annotations

import asyncio
import time
import weakref
from collections import OrderedDict
from contextlib import suppress
from pathlib import Path
from typing import Any

from .api_client import (
    CredentialInvalidError,
    SendUncertainError,
    XiaoheiheApiClient,
)
from .auth import AuthService, CredentialStore
from .config_service import ConfigService
from .database import Database
from .feed_service import FeedService
from .logging_service import LoggingService
from .models import EventState, RoutingTarget
from .repository import Repository
from .request_signing import ensure_client_identity
from .security import redact_text, sanitize_reply_text
from .task_manager import TaskManager

PLUGIN_NAME = "astrbot_plugin_xiaoheihe_adapter"

_runtime: RuntimeServices | None = None


def bind_runtime(runtime: RuntimeServices) -> None:
    global _runtime
    _runtime = runtime


def get_runtime() -> RuntimeServices:
    if _runtime is None:
        raise RuntimeError("????????????")
    return _runtime


def unbind_runtime(runtime: RuntimeServices) -> None:
    global _runtime
    if _runtime is runtime:
        _runtime = None


class RuntimeServices:
    def __init__(self, config: Any, data_dir: Path) -> None:
        self.data_dir = data_dir.resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.config = ConfigService(config)
        self.tasks = TaskManager()
        self.database = Database(self.data_dir / "xiaoheihe.db")
        self.repository = Repository(self.database)
        log_config = self.config.snapshot()["logging"]
        retention = self.config.snapshot()["retention"]
        self.logging = LoggingService(
            self.data_dir,
            self.tasks,
            level=str(log_config["level"]),
            max_memory_entries=int(log_config["max_memory_entries"]),
            total_limit_mb=int(retention["log_total_limit_mb"]),
        )
        self.credentials = CredentialStore(self.data_dir)
        self.auth = AuthService(
            self.credentials,
            self.repository,
            self.get_client,
            task_manager=self.tasks,
            on_login=self.notify_profile_changed,
        )
        self._clients: dict[tuple[str, bool], XiaoheiheApiClient] = {}
        self._started = False
        self._closed = False
        self._start_lock = asyncio.Lock()
        self._adapters: weakref.WeakSet[Any] = weakref.WeakSet()
        self._floor_locks: OrderedDict[str, asyncio.Lock] = OrderedDict()
        self._alerts: dict[str, dict[str, Any]] = {}
        self.last_cleanup_at: str | None = None
        self.config.add_restart_callback(self._on_config_changed)

    async def ensure_started(self) -> None:
        if self._started:
            return
        async with self._start_lock:
            if self._started:
                return
            if self._closed:
                raise RuntimeError("????????")
            await self.database.open()
            self._started = True
            await self.tasks.start("xhh-cleanup", self._cleanup_loop())
            await self.tasks.start("xhh-health", self._health_loop())
            self.logging.emit("INFO", "???????????")

    async def get_client(self, profile_id: str, anonymous: bool = False) -> XiaoheiheApiClient:
        await self.ensure_started()
        key = (profile_id, anonymous)
        credentials = None if anonymous else self.credentials.load(profile_id)
        if credentials is not None and ensure_client_identity(credentials):
            self.credentials.save(credentials)
        existing = self._clients.get(key)
        if existing and not existing.closed:
            existing_uid = existing.credentials.uid if existing.credentials else ""
            requested_uid = credentials.uid if credentials else ""
            if existing_uid == requested_uid:
                return existing
            await existing.close()
        config = self.config.snapshot()
        network = config["network"]
        reply = config["reply"]
        client = XiaoheiheApiClient(
            profile_id,
            credentials=credentials,
            request_timeout_seconds=float(network["request_timeout_seconds"]),
            connect_timeout_seconds=float(network["connect_timeout_seconds"]),
            read_timeout_seconds=float(network["read_timeout_seconds"]),
            min_request_interval_seconds=float(network["min_request_interval_seconds"]),
            max_retries=int(reply["max_retries"]),
            on_auth_invalid=self._on_auth_invalid,
        )
        await client.start()
        self._clients[key] = client
        return client

    async def invalidate_client(
        self,
        profile_id: str,
        *,
        include_anonymous: bool = True,
    ) -> None:
        for key in tuple(self._clients):
            if key[0] != profile_id or (key[1] and not include_anonymous):
                continue
            client = self._clients.pop(key)
            await client.close()

    def register_adapter(self, adapter: Any) -> None:
        self._adapters.add(adapter)

    def unregister_adapter(self, adapter: Any) -> None:
        self._adapters.discard(adapter)

    async def notify_profile_changed(
        self,
        profile_id: str,
        *,
        preserve_anonymous: bool = False,
    ) -> None:
        await self.invalidate_client(
            profile_id,
            include_anonymous=not preserve_anonymous,
        )
        state = await self.repository.account_state(profile_id)
        if state.get("status") in {
            "requesting_qr",
            "waiting_scan",
            "success",
            "logged_out",
        }:
            for suffix in ("401", "403", "429", "credential_invalid"):
                self._alerts.pop(f"{profile_id}:{suffix}", None)
        for adapter in tuple(self._adapters):
            if str(adapter.config.get("profile_id", "default")) == profile_id:
                adapter.request_refresh()

    async def deliver(
        self,
        *,
        event_id: int | None,
        route: RoutingTarget,
        content: str,
        dry_run: bool,
        generated_ms: int | None = None,
        proactive: bool = False,
    ) -> dict[str, Any]:
        config = self.config.snapshot()
        text = sanitize_reply_text(content, int(config["reply"]["max_reply_chars"]))
        if event_id is not None:
            await self.repository.mark_event(
                event_id,
                EventState.GENERATED,
                reply_text=text,
                generated_ms=generated_ms,
            )
        if dry_run:
            await self.repository.record_outgoing_attempt(
                route.profile_id,
                event_id,
                route,
                text,
                EventState.DRY_RUN.value,
            )
            if event_id is not None:
                if config["reply"].get("dry_run_mark_processed", True):
                    await self.repository.mark_event(
                        event_id,
                        EventState.DRY_RUN,
                        reply_text=text,
                    )
                else:
                    await self.repository.defer_event(
                        event_id,
                        "dry-run ???????????????",
                        delay_seconds=float(config["polling"]["poll_interval_seconds"]),
                    )
            return {"status": EventState.DRY_RUN.value, "text": text}

        lock = self._floor_lock(route)
        async with lock:
            client = await self.get_client(route.profile_id)
            if client.credentials is None:
                if event_id is not None:
                    await self.repository.mark_event(
                        event_id,
                        EventState.DEAD_LETTER,
                        error="?????????????",
                    )
                raise CredentialInvalidError("?????????????")
            outgoing_id = await self.repository.record_outgoing_attempt(
                route.profile_id,
                event_id,
                route,
                text,
                "sending",
            )
            attempted_at = time.time()
            try:
                result = await client.send_comment(route, text)
            except SendUncertainError as exc:
                await self.repository.mark_event(
                    event_id, EventState.SEND_UNKNOWN, error=str(exc)
                ) if event_id is not None else None
                await self.repository.db.execute(
                    """
                    UPDATE outgoing_replies
                    SET status = 'send_unknown', error = ?
                    WHERE id = ?
                    """,
                    (redact_text(str(exc))[:2000], outgoing_id),
                )
                confirmed_comment_id = await self._check_uncertain_send(
                    client,
                    route,
                    text,
                    outgoing_id,
                    attempted_at=attempted_at,
                )
                if confirmed_comment_id:
                    if event_id is not None:
                        await self.repository.mark_event(
                            event_id,
                            EventState.SENT,
                            reply_text=text,
                        )
                    if proactive:
                        await self.repository.increment_counter(route.profile_id, proactive=1)
                    else:
                        await self.repository.increment_counter(route.profile_id, reply=1)
                    return {
                        "status": EventState.SENT.value,
                        "external_comment_id": confirmed_comment_id,
                        "text": text,
                        "confirmed_after_timeout": True,
                    }
                raise
            except BaseException as exc:
                await self.repository.db.execute(
                    "UPDATE outgoing_replies SET status = 'failed', error = ? WHERE id = ?",
                    (redact_text(str(exc))[:2000], outgoing_id),
                )
                if event_id is not None:
                    await self.repository.schedule_retry(
                        event_id,
                        str(exc),
                        max_retries=int(config["reply"]["max_retries"]),
                    )
                raise
            await self.repository.confirm_outgoing(outgoing_id, result.external_comment_id)
            if event_id is not None:
                await self.repository.mark_event(event_id, EventState.SENT, reply_text=text)
            if proactive:
                await self.repository.increment_counter(route.profile_id, proactive=1)
            else:
                await self.repository.increment_counter(route.profile_id, reply=1)
            return {
                "status": EventState.SENT.value,
                "external_comment_id": result.external_comment_id,
                "text": text,
            }

    async def capture_feed_candidate(
        self,
        *,
        event_id: int,
        route: RoutingTarget,
        content: str,
        metadata: dict[str, Any],
        generated_ms: int | None = None,
    ) -> int | None:
        config = self.config.snapshot()
        text = sanitize_reply_text(content, int(config["reply"]["max_reply_chars"]))
        candidate_id = await self.repository.create_feed_candidate(
            route.profile_id,
            route.post_id,
            str(metadata.get("post_title", "")),
            str(metadata.get("post_author_uid", "")),
            text,
            str(metadata.get("candidate_reason", "AI ????")),
        )
        await self.repository.mark_event(
            event_id,
            EventState.GENERATED,
            reply_text=text,
            generated_ms=generated_ms,
        )
        return candidate_id

    async def expire_reply(
        self,
        *,
        event_id: int | None,
        route: RoutingTarget,
        generated_ms: int,
    ) -> None:
        error = (
            f"AstrBot ?????? {generated_ms}ms????????????????????????"
        )
        if event_id is not None:
            await self.repository.mark_event(
                event_id,
                EventState.DEAD_LETTER,
                error=error,
                generated_ms=generated_ms,
            )
        self.logging.emit(
            "WARNING",
            error,
            profile_id=route.profile_id,
            details={"session_id": route.session_id},
        )

    async def fail_reply(
        self,
        *,
        event_id: int | None,
        route: RoutingTarget,
        error: str,
        generated_ms: int,
    ) -> None:
        if event_id is not None:
            row = await self.repository.db.fetchone(
                "SELECT status FROM incoming_events WHERE id = ?",
                (event_id,),
            )
            status = str(row["status"]) if row else ""
            if status not in {
                EventState.RETRY_WAIT.value,
                EventState.SEND_UNKNOWN.value,
                EventState.SENT.value,
                EventState.DRY_RUN.value,
                EventState.DEAD_LETTER.value,
            }:
                await self.repository.mark_event(
                    event_id,
                    EventState.GENERATED,
                    error=error,
                    generated_ms=generated_ms,
                )
                await self.repository.schedule_retry(
                    event_id,
                    error,
                    max_retries=int(self.config.snapshot()["reply"]["max_retries"]),
                )
        self.logging.emit(
            "WARNING",
            error,
            profile_id=route.profile_id,
            details={"session_id": route.session_id},
        )

    def feed_service(
        self, profile_id: str, client: XiaoheiheApiClient, synthetic_dispatch
    ) -> FeedService:
        return FeedService(
            profile_id,
            self.config.snapshot(),
            client,
            self.repository,
            synthetic_dispatch,
            lambda route, text: self.deliver(
                event_id=None,
                route=route,
                content=text,
                dry_run=False,
                proactive=True,
            ),
        )

    def _floor_lock(self, route: RoutingTarget) -> asyncio.Lock:
        key = f"{route.profile_id}:{route.session_id}"
        lock = self._floor_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._floor_locks[key] = lock
        self._floor_locks.move_to_end(key)
        while len(self._floor_locks) > 1024:
            old_key, old_lock = next(iter(self._floor_locks.items()))
            if old_lock.locked():
                self._floor_locks.move_to_end(old_key)
                break
            self._floor_locks.pop(old_key, None)
        return lock

    async def _check_uncertain_send(
        self,
        client: XiaoheiheApiClient,
        route: RoutingTarget,
        content: str,
        outgoing_id: int,
        *,
        attempted_at: float,
    ) -> str | None:
        """Check recent comments, but never infer absence from an unverified API."""

        try:
            comments = await client.recent_comments(route, limit=30)
        except BaseException as exc:
            self.logging.emit(
                "ERROR",
                f"???????????: {exc}",
                profile_id=route.profile_id,
            )
            return None
        self_uid = client.credentials.uid if client.credentials else ""
        normalized = sanitize_reply_text(
            content, int(self.config.snapshot()["reply"]["max_reply_chars"])
        )
        for comment in comments:
            user = comment.get("user", comment.get("sender", {}))
            user = user if isinstance(user, dict) else {}
            uid = str(
                user.get("uid", user.get("heybox_id", user.get("user_id", user.get("id", ""))))
            )
            candidate = str(comment.get("content", comment.get("text", ""))).strip()
            comment_id = str(comment.get("comment_id", comment.get("id", "")))
            timestamp_value = comment.get(
                "created_at",
                comment.get("timestamp", comment.get("time")),
            )
            try:
                comment_time = float(timestamp_value)
                if comment_time > 10_000_000_000:
                    comment_time /= 1000
            except (TypeError, ValueError):
                continue
            within_window = attempted_at - 600 <= comment_time <= time.time() + 120
            if (
                self_uid
                and uid == self_uid
                and candidate == normalized
                and comment_id
                and within_window
            ):
                await self.repository.confirm_outgoing(outgoing_id, comment_id)
                self.logging.emit(
                    "WARNING",
                    "??????????????????????????",
                    profile_id=route.profile_id,
                )
                return comment_id
        self.logging.emit(
            "WARNING",
            "??????????????????????????????????",
            profile_id=route.profile_id,
        )
        return None

    async def status(self) -> dict[str, Any]:
        await self.ensure_started()
        profiles = []
        alerts = dict(self._alerts)
        for profile in self.config.enabled_profiles():
            profile_id = str(profile["profile_id"])
            state = await self.repository.account_state(profile_id)
            counters = await self.repository.today_counters(profile_id)
            client = self._clients.get((profile_id, False))
            if client is not None:
                state["last_success_request_at"] = client.last_success_at or state.get(
                    "last_success_request_at"
                )
                if client.last_error:
                    state["last_client_error"] = client.last_error
                    if client.last_error.get("category") == "response_shape":
                        alerts[f"{profile_id}:response_shape"] = {
                            "key": f"{profile_id}:response_shape",
                            "level": "error",
                            "message": f"?? {profile_id} ???? API ?????????",
                        }
                state["consecutive_429"] = max(
                    int(state.get("consecutive_429") or 0),
                    client.consecutive_status[429],
                )
                state["notification_polls"] = client.last_notification_polls
            if state.get("status") == "credential_invalid":
                alerts[f"{profile_id}:credential_invalid"] = {
                    "key": f"{profile_id}:credential_invalid",
                    "level": "error",
                    "message": f"?? {profile_id} ???????",
                }
            if int(state.get("consecutive_poll_failures") or 0) >= 3:
                reason = str(state.get("last_error") or "???????")
                alerts[f"{profile_id}:polling"] = {
                    "key": f"{profile_id}:polling",
                    "level": "error",
                    "message": f"?? {profile_id} ???????{reason[:300]}",
                }
            if int(state.get("consecutive_429") or 0) >= 3:
                alerts[f"{profile_id}:429"] = {
                    "key": f"{profile_id}:429",
                    "level": "warning",
                    "message": f"?? {profile_id} ???? HTTP 429 ??",
                }
            profiles.append(
                {
                    **state,
                    **counters,
                    "dry_run": bool(profile.get("dry_run", True)),
                    "has_credentials": self.credentials.exists(profile_id),
                }
            )
        task_failures = self.tasks.failures()
        if task_failures:
            latest_failure = task_failures[-1]
            alerts["background_task"] = {
                "key": "background_task",
                "level": "error",
                "message": (f"??????: {latest_failure['task']} ? {latest_failure['error']}"),
            }
        return {
            "version": "v1.0.7",
            "profiles": profiles,
            "adapters": [
                {
                    "id": adapter.config.get("id", "xiaoheihe"),
                    "profile_id": adapter.config.get("profile_id", "default"),
                    "running": adapter.running,
                }
                for adapter in tuple(self._adapters)
            ],
            "tasks": self.tasks.task_names(),
            "task_failures": task_failures,
            "queue_length": sum(
                getattr(adapter, "queue_length", 0) for adapter in tuple(self._adapters)
            ),
            "database_size": self.repository.database_size(),
            "log_size": self.logging.total_size(),
            "alerts": list(alerts.values()),
            "last_cleanup_at": self.last_cleanup_at,
        }

    async def _on_auth_invalid(self, profile_id: str, status_code: int) -> None:
        field = f"consecutive_{status_code}"
        state = await self.repository.account_state(profile_id)
        count = int(state.get(field, 0)) + 1
        circuit_seconds = 300 if status_code == 401 else 600
        await self.repository.update_account_state(
            profile_id,
            status="credential_invalid" if status_code == 401 else "failed",
            **{
                field: count,
                "circuit_open_until": time.time() + circuit_seconds,
                "last_error": f"?? HTTP {status_code}",
            },
        )
        self._alerts[f"{profile_id}:{status_code}"] = {
            "key": f"{profile_id}:{status_code}",
            "level": "error",
            "message": f"?? {profile_id} ???? HTTP {status_code}????????",
        }
        await self.notify_profile_changed(profile_id)

    async def _on_config_changed(self, changed: set[str]) -> None:
        snapshot = self.config.snapshot()
        if changed & {"logging", "retention"}:
            logging_config = snapshot["logging"]
            self.logging.reconfigure(
                level=str(logging_config["level"]),
                max_memory_entries=int(logging_config["max_memory_entries"]),
                total_limit_mb=int(snapshot["retention"]["log_total_limit_mb"]),
            )
        if changed & {"network", "reply"}:
            clients = tuple(self._clients.values())
            self._clients.clear()
            for client in clients:
                await client.close()
        self.logging.emit(
            "INFO",
            "??????????????????",
            details={"changed_sections": sorted(changed)},
        )
        for adapter in tuple(self._adapters):
            adapter.request_refresh()

    def report_vision_degraded(self, profile_id: str, image_count: int) -> None:
        key = "vision_unsupported"
        if key not in self._alerts:
            self.logging.emit(
                "WARNING",
                "?????????????????????????",
                profile_id=profile_id,
                details={"omitted_image_count": image_count},
            )
        self._alerts[key] = {
            "key": key,
            "level": "warning",
            "message": "??????????????????????????",
        }

    def clear_vision_alert(self) -> None:
        self._alerts.pop("vision_unsupported", None)

    def report_proactive_circuit(self, profile_id: str, error: BaseException) -> None:
        self.logging.emit(
            "ERROR",
            f"????????????? 300 ?: {error}",
            profile_id=profile_id,
        )
        self._alerts[f"{profile_id}:proactive_circuit"] = {
            "key": f"{profile_id}:proactive_circuit",
            "level": "error",
            "message": f"?? {profile_id} ????????????",
        }

    def clear_proactive_circuit(self, profile_id: str) -> None:
        self._alerts.pop(f"{profile_id}:proactive_circuit", None)

    async def _cleanup_loop(self) -> None:
        await self._cleanup_sleep(60)
        while True:
            retry_delay = 86400
            try:
                await self._run_cleanup_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                safe_error = redact_text(str(exc))[:1000] or type(exc).__name__
                retry_delay = 3600
                self._alerts["cleanup"] = {
                    "key": "cleanup",
                    "level": "warning",
                    "message": f"????????????????{safe_error}",
                }
                self.logging.emit(
                    "ERROR",
                    f"?????????{safe_error}",
                    details={"exception_type": type(exc).__name__},
                )
                with suppress(Exception):
                    await self.repository.add_runtime_error(
                        "cleanup",
                        safe_error,
                        details={"exception_type": type(exc).__name__},
                    )
            await self._cleanup_sleep(retry_delay)

    async def _cleanup_sleep(self, delay: float) -> None:
        await asyncio.sleep(delay)

    async def _run_cleanup_once(self) -> None:
        config = self.config.snapshot()
        removed = await self.repository.cleanup(config["retention"])
        pruned = await self.repository.prune_event_bodies(config["retention"])
        pressure = await self.repository.enforce_soft_limit(config["retention"])
        self.last_cleanup_at = _iso_now()
        self._alerts.pop("cleanup", None)
        self.logging.emit(
            "INFO",
            "????????",
            details={
                "removed": removed,
                "pruned_bodies": pruned,
                "storage_pressure": pressure,
            },
        )

    async def _health_loop(self) -> None:
        while True:
            try:
                await self.repository.db.fetchone("SELECT 1 AS ok")
                size_mb = self.repository.database_size() / 1024 / 1024
                retention = self.config.snapshot()["retention"]
                warn_mb = float(retention["database_warn_mb"])
                key = "database_size"
                if size_mb >= warn_mb:
                    self._alerts[key] = {
                        "key": key,
                        "level": "warning",
                        "message": f"?????? {size_mb:.1f} MB",
                    }
                else:
                    self._alerts.pop(key, None)
                soft_mb = float(retention["database_soft_limit_mb"])
                if size_mb >= soft_mb:
                    self._alerts["database_soft_limit"] = {
                        "key": "database_soft_limit",
                        "level": "error",
                        "message": f"?????? {soft_mb:.0f} MB ???????????????",
                    }
                else:
                    self._alerts.pop("database_soft_limit", None)
            except BaseException as exc:
                self._alerts["database"] = {
                    "key": "database",
                    "level": "error",
                    "message": f"?????????????: {exc}",
                }
            await asyncio.sleep(60)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for adapter in tuple(self._adapters):
            with suppress(BaseException):
                await adapter.terminate()
        await self.tasks.close()
        for client in tuple(self._clients.values()):
            with suppress(BaseException):
                await client.close()
        self._clients.clear()
        if self._started:
            await self.database.close()
        self.logging.close()
        self._floor_locks.clear()
        self._started = False
        unbind_runtime(self)


def _iso_now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()
