from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator
from typing import Any

from astrbot.api.event import AstrMessageEvent, MessageChain
from astrbot.api.message_components import Plain
from astrbot.api.platform import AstrBotMessage, PlatformMetadata

from .models import RoutingTarget
from .runtime import RuntimeServices

CONTROL_MESSAGE_TYPES = frozenset(
    {
        "agent_stats",
        "break",
        "reasoning",
        "tool_call",
        "tts_stats",
    }
)
DIRECT_RESULT_MESSAGE_TYPES = frozenset({"tool_direct_result"})


def aggregate_text(message: MessageChain) -> str:
    return _aggregate_components(message.chain)


def _aggregate_components(components: list[Any]) -> str:
    parts: list[str] = []
    for component in components:
        if isinstance(component, Plain):
            parts.append(str(component.text))
    return "".join(parts).strip()


class XiaoheiheMessageEvent(AstrMessageEvent):
    def __init__(
        self,
        message_str: str,
        message_obj: AstrBotMessage,
        platform_meta: PlatformMetadata,
        session_id: str,
        *,
        runtime: RuntimeServices,
        route: RoutingTarget,
        event_id: int | None,
        dry_run: bool,
        proactive: bool = False,
        capture_candidate: bool = False,
        candidate_metadata: dict[str, Any] | None = None,
        reply_timeout_seconds: int = 120,
    ) -> None:
        super().__init__(message_str, message_obj, platform_meta, session_id)
        self.runtime = runtime
        self.route = route
        self.event_id = event_id
        self.dry_run = dry_run
        self.proactive = proactive
        self.capture_candidate = capture_candidate
        self.candidate_metadata = candidate_metadata or {}
        self.reply_timeout_seconds = max(5, int(reply_timeout_seconds))
        self._send_lock = asyncio.Lock()
        self._completion = asyncio.Event()
        self._final_send_done = False
        self._agent_started = False
        self._agent_done = False
        self._agent_final_text = ""
        self._direct_reply_texts: list[str] = []
        self._agent_intermediate_texts: list[str] = []
        self._started_at = time.perf_counter()

    def mark_agent_started(self) -> None:
        if not self._final_send_done:
            self._agent_started = True

    def mark_agent_done(self, final_text: str = "") -> None:
        if self._final_send_done:
            return
        self._agent_started = True
        self._agent_done = True
        text = str(final_text or "").strip()
        if text:
            self._agent_final_text = text

    async def send(self, message: MessageChain) -> None:
        message_type = str(getattr(message, "type", "") or "").strip().lower()
        received_text = aggregate_text(message)
        async with self._send_lock:
            if self._final_send_done:
                self.runtime.logging.emit(
                    "DEBUG",
                    "AstrBot 分段回复的后续片段已由完整结果覆盖",
                    profile_id=self.route.profile_id,
                )
                return

            if message_type in CONTROL_MESSAGE_TYPES:
                self.runtime.logging.emit(
                    "DEBUG",
                    "已暂存 AstrBot Agent 控制消息，等待最终回复",
                    profile_id=self.route.profile_id,
                    details={"message_type": message_type},
                )
                return

            if self._agent_done and not self._is_final_agent_delivery(received_text):
                if received_text:
                    self._append_distinct(self._direct_reply_texts, received_text)
                self.runtime.logging.emit(
                    "DEBUG",
                    "已暂存 Agent 完成钩子产生的附加回复，等待最终发送阶段",
                    profile_id=self.route.profile_id,
                    details={"message_type": message_type or "default"},
                )
                return

            if self._should_wait_for_agent():
                if received_text:
                    target = (
                        self._direct_reply_texts
                        if message_type in DIRECT_RESULT_MESSAGE_TYPES or not self._agent_started
                        else self._agent_intermediate_texts
                    )
                    self._append_distinct(target, received_text)
                self.runtime.logging.emit(
                    "DEBUG",
                    "已暂存 AstrBot Agent 中间回复，等待 Agent 完成",
                    profile_id=self.route.profile_id,
                    details={
                        "message_type": message_type or "default",
                        "has_text": bool(received_text),
                    },
                )
                return

            complete_text = self._complete_segmented_text(received_text)
            segmented_reply = bool(complete_text and complete_text != received_text)
            text = self._compose_final_text(complete_text)
            final_message = message
            if text and text != received_text:
                final_message = MessageChain([Plain(text)])
            if segmented_reply:
                self.runtime.report_segmented_reply_aggregated(self.route.profile_id)
            generated_ms = max(0, int((time.perf_counter() - self._started_at) * 1000))
            try:
                if not text:
                    await self.runtime.fail_reply(
                        event_id=self.event_id,
                        route=self.route,
                        error="AstrBot 原生管线返回空回复",
                        generated_ms=generated_ms,
                    )
                    return
                if generated_ms > self.reply_timeout_seconds * 1000:
                    await self.runtime.expire_reply(
                        event_id=self.event_id,
                        route=self.route,
                        generated_ms=generated_ms,
                        reply_timeout_seconds=self.reply_timeout_seconds,
                    )
                    return
                if self.capture_candidate:
                    if self.event_id is None:
                        raise RuntimeError("主动刷帖事件缺少持久化 event_id")
                    await self.runtime.capture_feed_candidate(
                        event_id=self.event_id,
                        route=self.route,
                        content=text,
                        metadata=self.candidate_metadata,
                        generated_ms=generated_ms,
                    )
                else:
                    await self.runtime.deliver(
                        event_id=self.event_id,
                        route=self.route,
                        content=text,
                        dry_run=self.dry_run,
                        generated_ms=generated_ms,
                        proactive=self.proactive,
                    )
                await super().send(final_message)
            except asyncio.CancelledError as exc:
                await self.runtime.fail_reply(
                    event_id=self.event_id,
                    route=self.route,
                    error=str(exc) or "事件发送任务被取消",
                    generated_ms=generated_ms,
                )
                raise
            except Exception as exc:
                await self.runtime.fail_reply(
                    event_id=self.event_id,
                    route=self.route,
                    error=str(exc),
                    generated_ms=generated_ms,
                )
                raise
            finally:
                self._final_send_done = True
                self._completion.set()

    async def send_streaming(
        self,
        generator: AsyncGenerator[MessageChain, None],
        use_fallback: bool = False,
    ) -> None:
        combined: list[str] = []
        async for chain in generator:
            text = aggregate_text(chain)
            if text:
                combined.append(text)
        if combined:
            await self.send(MessageChain([Plain("".join(combined))]))
        else:
            await self.send(MessageChain([]))

    async def wait_finished(self) -> None:
        await self._completion.wait()

    async def expire(self) -> None:
        async with self._send_lock:
            if self._final_send_done:
                return
            generated_ms = max(0, int((time.perf_counter() - self._started_at) * 1000))
            await self.runtime.expire_reply(
                event_id=self.event_id,
                route=self.route,
                generated_ms=generated_ms,
                reply_timeout_seconds=self.reply_timeout_seconds,
            )
            self._final_send_done = True
            self._completion.set()

    def _should_wait_for_agent(self) -> bool:
        if self._agent_done:
            return False
        if self._agent_started:
            return True
        return bool(getattr(self, "call_llm", False))

    def _is_final_agent_delivery(self, received_text: str) -> bool:
        normalized_received = _normalize_text(received_text)
        normalized_final = _normalize_text(self._agent_final_text)
        if normalized_received and normalized_final:
            if normalized_received in normalized_final or normalized_final in normalized_received:
                return True

        get_result = getattr(self, "get_result", None)
        if not callable(get_result):
            return False
        try:
            result = get_result()
        except (AttributeError, RuntimeError):
            return False
        is_model_result = getattr(result, "is_model_result", None)
        if callable(is_model_result):
            try:
                return bool(is_model_result())
            except (AttributeError, RuntimeError):
                return False
        result_type = str(getattr(result, "result_content_type", "") or "").lower()
        return "llm" in result_type or "streaming" in result_type

    @staticmethod
    def _append_distinct(items: list[str], text: str) -> None:
        candidate = text.strip()
        if not candidate:
            return
        normalized = _normalize_text(candidate)
        if any(_normalize_text(item) == normalized for item in items):
            return
        items.append(candidate)

    def _compose_final_text(self, final_text: str) -> str:
        canonical = final_text.strip() or self._agent_final_text
        if not self._agent_done:
            return canonical

        parts: list[str] = []
        if canonical:
            parts.append(canonical)
        for direct_text in self._direct_reply_texts:
            self._append_non_overlapping(parts, direct_text)
        if parts:
            return "\n\n".join(parts)
        return canonical

    @staticmethod
    def _append_non_overlapping(parts: list[str], candidate: str) -> None:
        normalized = _normalize_text(candidate)
        if not normalized:
            return
        for index, existing in enumerate(parts):
            normalized_existing = _normalize_text(existing)
            if normalized in normalized_existing:
                return
            if normalized_existing in normalized:
                parts[index] = candidate.strip()
                return
        parts.append(candidate.strip())

    def _complete_segmented_text(self, received_text: str) -> str:
        if not received_text:
            return self._agent_final_text
        get_extra = getattr(self, "get_extra", None)
        if callable(get_extra):
            captured_text = str(get_extra("xiaoheihe_complete_reply_text", "") or "").strip()
            if len(captured_text) > len(received_text) and received_text in captured_text:
                return captured_text
        get_result = getattr(self, "get_result", None)
        if callable(get_result):
            try:
                result = get_result()
            except (AttributeError, RuntimeError):
                result = None
            chain = getattr(result, "chain", None)
            if isinstance(chain, list):
                complete_text = _aggregate_components(chain)
                if len(complete_text) > len(received_text) and received_text in complete_text:
                    return complete_text
        return received_text


def _normalize_text(text: str) -> str:
    return " ".join(str(text).split()).casefold()
