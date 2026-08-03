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
        self._started_at = time.perf_counter()

    async def send(self, message: MessageChain) -> None:
        received_text = aggregate_text(message)
        text = self._complete_segmented_text(received_text)
        final_message = message
        segmented_reply = bool(text and text != received_text)
        if segmented_reply:
            final_message = MessageChain([Plain(text)])
        async with self._send_lock:
            if self._final_send_done:
                self.runtime.logging.emit(
                    "DEBUG",
                    "AstrBot 分段回复的后续片段已由完整结果覆盖",
                    profile_id=self.route.profile_id,
                )
                return
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

    def _complete_segmented_text(self, received_text: str) -> str:
        if not received_text:
            return received_text
        get_extra = getattr(self, "get_extra", None)
        if callable(get_extra):
            captured_text = str(get_extra("xiaoheihe_complete_reply_text", "") or "").strip()
            if len(captured_text) > len(received_text) and received_text in captured_text:
                return captured_text
        get_result = getattr(self, "get_result", None)
        if not callable(get_result):
            return received_text
        try:
            result = get_result()
        except (AttributeError, RuntimeError):
            return received_text
        chain = getattr(result, "chain", None)
        if not isinstance(chain, list):
            return received_text
        complete_text = _aggregate_components(chain)
        if len(complete_text) > len(received_text) and received_text in complete_text:
            return complete_text
        return received_text
