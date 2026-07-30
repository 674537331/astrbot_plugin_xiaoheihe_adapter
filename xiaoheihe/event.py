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
    parts: list[str] = []
    for component in message.chain:
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
        capture_candidate: bool = False,
        candidate_metadata: dict[str, Any] | None = None,
        reply_timeout_seconds: int = 120,
    ) -> None:
        super().__init__(message_str, message_obj, platform_meta, session_id)
        self.runtime = runtime
        self.route = route
        self.event_id = event_id
        self.dry_run = dry_run
        self.capture_candidate = capture_candidate
        self.candidate_metadata = candidate_metadata or {}
        self.reply_timeout_seconds = max(5, int(reply_timeout_seconds))
        self._send_lock = asyncio.Lock()
        self._completion = asyncio.Event()
        self._final_send_done = False
        self._started_at = time.perf_counter()

    async def send(self, message: MessageChain) -> None:
        text = aggregate_text(message)
        async with self._send_lock:
            if self._final_send_done:
                self.runtime.logging.emit(
                    "DEBUG",
                    "检测到同一入站事件的额外消息段，已按单评论策略忽略",
                    profile_id=self.route.profile_id,
                )
                return
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
                    )
                await super().send(message)
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
            )
            self._final_send_done = True
            self._completion.set()
