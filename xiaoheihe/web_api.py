from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from typing import Any

from astrbot.api.web import error_response, json_response, request, stream_response

from .config_service import DEFAULT_CONFIG, ConfigValidationError
from .runtime import PLUGIN_NAME, RuntimeServices
from .security import SecurityError, redact_data, redact_text, validate_profile_id


class WebApiController:
    def __init__(self, runtime: RuntimeServices) -> None:
        self.runtime = runtime

    def register(self, context) -> None:
        routes = (
            ("status", self.status, ["GET"], "小黑盒状态总览"),
            ("auth/qr", self.auth_qr, ["POST"], "获取小黑盒登录二维码"),
            ("auth/status", self.auth_status, ["GET"], "读取登录状态"),
            ("auth/check", self.auth_check, ["POST"], "检查扫码或凭证"),
            ("auth/logout", self.auth_logout, ["POST"], "安全登出"),
            ("config", self.config_get, ["GET"], "读取插件配置"),
            ("config/save", self.config_save, ["POST"], "保存插件配置"),
            ("config/defaults", self.config_defaults, ["GET"], "读取默认配置"),
            ("events", self.events, ["GET"], "查询事件记录"),
            ("feed/candidates", self.feed_candidates, ["GET"], "查询审核候选"),
            (
                "feed/candidates/<candidate_id>/approve",
                self.feed_approve,
                ["POST"],
                "批准主动回复候选",
            ),
            (
                "feed/candidates/<candidate_id>/reject",
                self.feed_reject,
                ["POST"],
                "拒绝主动回复候选",
            ),
            (
                "feed/candidates/reject-expired",
                self.feed_reject_expired,
                ["POST"],
                "批量拒绝过期候选",
            ),
            ("logs", self.logs, ["GET"], "查询脱敏结构化日志"),
            ("logs/stream", self.logs_stream, ["GET"], "订阅脱敏日志 SSE"),
            ("storage", self.storage, ["GET"], "读取存储状态"),
            ("storage/cleanup-preview", self.cleanup_preview, ["GET"], "预览清理"),
            ("storage/cleanup", self.cleanup_run, ["POST"], "执行安全清理"),
            ("diagnostics", self.diagnostics, ["GET"], "导出脱敏诊断"),
        )
        for suffix, handler, methods, description in routes:
            context.register_web_api(
                f"/{PLUGIN_NAME}/{suffix}",
                handler,
                methods,
                description,
            )

    def _unauthorized(self):
        if request.username is None:
            return error_response("需要已认证的 AstrBot Dashboard 会话", status_code=401)
        return None

    async def status(self):
        if response := self._unauthorized():
            return response
        try:
            return json_response(await self.runtime.status())
        except BaseException as exc:
            return error_response(f"读取状态失败: {_safe_error(exc)}", status_code=500)

    async def auth_qr(self):
        if response := self._unauthorized():
            return response
        payload = await request.json(default={})
        try:
            profile_id = validate_profile_id(str(payload.get("profile_id", "default")))
            self.runtime.config.profile(profile_id)
            await self.runtime.ensure_started()
            result = await self.runtime.auth.request_qr(profile_id)
            return json_response(result)
        except (SecurityError, ConfigValidationError, ValueError) as exc:
            return error_response(str(exc), status_code=400)
        except BaseException as exc:
            return error_response(f"获取二维码失败: {_safe_error(exc)}", status_code=502)

    async def auth_status(self):
        if response := self._unauthorized():
            return response
        try:
            profile_id = validate_profile_id(str(request.query.get("profile_id", "default")))
            await self.runtime.ensure_started()
            return json_response(await self.runtime.auth.status(profile_id))
        except (SecurityError, ValueError) as exc:
            return error_response(str(exc), status_code=400)

    async def auth_check(self):
        if response := self._unauthorized():
            return response
        payload = await request.json(default={})
        try:
            profile_id = validate_profile_id(str(payload.get("profile_id", "default")))
            await self.runtime.ensure_started()
            result = await self.runtime.auth.check(profile_id)
            if result.get("state") == "success":
                await self.runtime.notify_profile_changed(profile_id)
            return json_response(result)
        except (SecurityError, ValueError) as exc:
            return error_response(str(exc), status_code=400)
        except BaseException as exc:
            return error_response(f"登录检查失败: {_safe_error(exc)}", status_code=502)

    async def auth_logout(self):
        if response := self._unauthorized():
            return response
        payload = await request.json(default={})
        try:
            profile_id = validate_profile_id(str(payload.get("profile_id", "default")))
            await self.runtime.ensure_started()
            result = await self.runtime.auth.logout(profile_id)
            await self.runtime.notify_profile_changed(profile_id)
            return json_response(result)
        except (SecurityError, ValueError) as exc:
            return error_response(str(exc), status_code=400)

    async def config_get(self):
        if response := self._unauthorized():
            return response
        return json_response(self.runtime.config.snapshot())

    async def config_defaults(self):
        if response := self._unauthorized():
            return response
        return json_response(self.runtime.config.defaults())

    async def config_save(self):
        if response := self._unauthorized():
            return response
        payload = await request.json(default={})
        if not isinstance(payload, dict):
            return error_response("配置请求体必须是对象", status_code=400)
        unknown = set(payload) - set(DEFAULT_CONFIG)
        if unknown:
            return error_response(
                f"包含不允许的配置分组: {', '.join(sorted(unknown))}",
                status_code=400,
            )
        try:
            changed = await self.runtime.config.save(payload)
            return json_response({"saved": True, "changed": sorted(changed)})
        except (ConfigValidationError, SecurityError, ValueError) as exc:
            return error_response(f"配置校验失败: {exc}", status_code=400)
        except BaseException as exc:
            return error_response(f"配置保存失败: {_safe_error(exc)}", status_code=500)

    async def events(self):
        if response := self._unauthorized():
            return response
        try:
            result = await self.runtime.repository.list_events(
                status=str(request.query.get("status", "")),
                uid=str(request.query.get("uid", "")),
                post_id=str(request.query.get("post_id", "")),
                keyword=str(request.query.get("keyword", "")),
                start_time=_optional_float(request.query.get("start_time")),
                end_time=_optional_float(request.query.get("end_time")),
                page=int(request.query.get("page", 1, type=int)),
                page_size=int(request.query.get("page_size", 30, type=int)),
            )
            return json_response(result)
        except (TypeError, ValueError) as exc:
            return error_response(str(exc), status_code=400)

    async def feed_candidates(self):
        if response := self._unauthorized():
            return response
        try:
            items = await self.runtime.repository.list_feed_candidates(
                status=str(request.query.get("status", "pending")),
                limit=int(request.query.get("limit", 100, type=int)),
            )
            return json_response({"items": items})
        except ValueError as exc:
            return error_response(str(exc), status_code=400)

    async def feed_approve(self, candidate_id: str):
        if response := self._unauthorized():
            return response
        payload = await request.json(default={})
        try:
            numeric_id = _positive_int(candidate_id, "candidate_id")
            candidate = await self.runtime.repository.feed_candidate(numeric_id)
            if not candidate:
                return error_response("候选不存在", status_code=404)
            profile_id = validate_profile_id(str(candidate["profile_id"]))
            client = await self.runtime.get_client(profile_id)
            feed = self.runtime.feed_service(profile_id, client, self._unused_synthetic_dispatch)
            result = await feed.approve(
                numeric_id,
                str(payload.get("edited_text", "")).strip() or None,
            )
            return json_response({"approved": True, "result": result})
        except (SecurityError, ValueError) as exc:
            return error_response(str(exc), status_code=400)
        except BaseException as exc:
            return error_response(f"批准候选失败: {_safe_error(exc)}", status_code=502)

    async def feed_reject(self, candidate_id: str):
        if response := self._unauthorized():
            return response
        try:
            numeric_id = _positive_int(candidate_id, "candidate_id")
            changed = await self.runtime.repository.review_feed_candidate(numeric_id, "rejected")
            if not changed:
                return error_response("候选不存在或已处理", status_code=409)
            return json_response({"rejected": True})
        except ValueError as exc:
            return error_response(str(exc), status_code=400)

    async def feed_reject_expired(self):
        if response := self._unauthorized():
            return response
        payload = await request.json(default={})
        hours = int(payload.get("older_than_hours", 72))
        if not 1 <= hours <= 24 * 365:
            return error_response("older_than_hours 超出范围", status_code=400)
        count = 0
        candidates = await self.runtime.repository.list_feed_candidates(status="pending", limit=500)
        cutoff = time.time() - hours * 3600
        for candidate in candidates:
            if float(candidate["created_at"]) < cutoff:
                changed = await self.runtime.repository.review_feed_candidate(
                    int(candidate["id"]), "expired"
                )
                count += int(changed)
        return json_response({"rejected": count})

    async def logs(self):
        if response := self._unauthorized():
            return response
        items = self.runtime.logging.list(
            level=str(request.query.get("level", "")),
            keyword=str(request.query.get("keyword", "")),
            limit=int(request.query.get("limit", 200, type=int)),
        )
        return json_response({"items": items})

    async def logs_stream(self):
        if response := self._unauthorized():
            return response

        async def stream():
            async for event in self.runtime.tasks.sse_events(max_queue=100):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

        return stream_response(stream())

    async def storage(self):
        if response := self._unauthorized():
            return response
        await self.runtime.ensure_started()
        return json_response(
            {
                "database_size": self.runtime.repository.database_size(),
                "log_size": self.runtime.logging.total_size(),
                "counts": await self.runtime.repository.table_counts(),
                "last_cleanup_at": self.runtime.last_cleanup_at,
            }
        )

    async def cleanup_preview(self):
        if response := self._unauthorized():
            return response
        await self.runtime.ensure_started()
        preview = await self.runtime.repository.cleanup_preview(
            self.runtime.config.snapshot()["retention"]
        )
        return json_response({"preview": preview})

    async def cleanup_run(self):
        if response := self._unauthorized():
            return response
        payload = await request.json(default={})
        if payload.get("confirm") is not True:
            return error_response("必须明确传入 confirm=true", status_code=400)
        await self.runtime.ensure_started()
        retention = self.runtime.config.snapshot()["retention"]
        removed = await self.runtime.repository.cleanup(retention)
        pruned = await self.runtime.repository.prune_event_bodies(retention)
        pressure = await self.runtime.repository.enforce_soft_limit(retention)
        self.runtime.last_cleanup_at = datetime.now(UTC).isoformat()
        return json_response(
            {
                "removed": removed,
                "pruned_bodies": pruned,
                "storage_pressure": pressure,
            }
        )

    async def diagnostics(self):
        if response := self._unauthorized():
            return response
        await self.runtime.ensure_started()
        payload = {
            "generated_at": datetime.now(UTC).isoformat(),
            "plugin": {"name": PLUGIN_NAME, "version": "v1.0.5"},
            "status": await self.runtime.status(),
            "storage": await self.runtime.repository.diagnostic_snapshot(),
            "logs": self.runtime.logging.list(limit=100),
            "config": redact_data(self.runtime.config.snapshot()),
        }
        return json_response(payload)

    async def _unused_synthetic_dispatch(self, notification, metadata: dict[str, Any]) -> None:
        raise RuntimeError(f"审核发送不应创建合成事件: {notification.post_id} {metadata!r}")


def _positive_int(value: str, name: str) -> int:
    candidate = int(value)
    if candidate <= 0:
        raise ValueError(f"{name} 必须是正整数")
    return candidate


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def _safe_error(error: BaseException) -> str:
    return redact_text(str(error))[:1000]
