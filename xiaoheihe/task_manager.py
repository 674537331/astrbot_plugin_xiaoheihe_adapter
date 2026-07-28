from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator, Awaitable
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any

from .security import redact_text


class TaskManager:
    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task[Any]] = {}
        self._profile_pollers: dict[str, str] = {}
        self._sse_subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._failures: deque[dict[str, str]] = deque(maxlen=50)
        self._closed = False
        self._lock = asyncio.Lock()

    @property
    def closed(self) -> bool:
        return self._closed

    def task_names(self) -> list[str]:
        return sorted(name for name, task in self._tasks.items() if not task.done())

    def failures(self) -> list[dict[str, str]]:
        return list(self._failures)

    async def start(
        self,
        name: str,
        awaitable: Awaitable[Any],
        *,
        replace: bool = False,
    ) -> asyncio.Task[Any]:
        async with self._lock:
            if self._closed:
                if hasattr(awaitable, "close"):
                    awaitable.close()  # type: ignore[attr-defined]
                raise RuntimeError("TaskManager 已关闭")
            existing = self._tasks.get(name)
            if existing and not existing.done():
                if not replace:
                    if hasattr(awaitable, "close"):
                        awaitable.close()  # type: ignore[attr-defined]
                    return existing
                existing.cancel()
                with suppress(asyncio.CancelledError):
                    await existing
            task = asyncio.create_task(awaitable, name=name)
            self._tasks[name] = task
            task.add_done_callback(lambda completed, key=name: self._drop(key, completed))
            return task

    async def start_profile_poller(
        self, profile_id: str, awaitable: Awaitable[Any]
    ) -> asyncio.Task[Any]:
        name = f"xhh-poll-{profile_id}"
        async with self._lock:
            existing_name = self._profile_pollers.get(profile_id)
            if existing_name:
                existing = self._tasks.get(existing_name)
                if existing and not existing.done():
                    if hasattr(awaitable, "close"):
                        awaitable.close()  # type: ignore[attr-defined]
                    return existing
            if self._closed:
                if hasattr(awaitable, "close"):
                    awaitable.close()  # type: ignore[attr-defined]
                raise RuntimeError("TaskManager 已关闭")
            task = asyncio.create_task(awaitable, name=name)
            self._tasks[name] = task
            self._profile_pollers[profile_id] = name
            task.add_done_callback(lambda completed, key=name: self._drop(key, completed))
            return task

    def _drop(self, name: str, task: asyncio.Task[Any]) -> None:
        current = self._tasks.get(name)
        if current is task:
            self._tasks.pop(name, None)
        for profile_id, poller_name in tuple(self._profile_pollers.items()):
            if poller_name == name:
                self._profile_pollers.pop(profile_id, None)
        if task.cancelled():
            return
        with suppress(asyncio.CancelledError):
            error = task.exception()
            if error is not None:
                failure = {
                    "task": name,
                    "error": redact_text(str(error))[:1000],
                    "time": datetime.now(UTC).isoformat(),
                }
                self._failures.append(failure)
                self.publish_sse({"type": "task_error", "failure": failure})

    async def cancel(self, name: str) -> None:
        async with self._lock:
            task = self._tasks.pop(name, None)
        if task and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    async def cancel_prefix(self, prefix: str) -> None:
        for name in tuple(self._tasks):
            if name.startswith(prefix):
                await self.cancel(name)

    def publish_sse(self, event: dict[str, Any]) -> None:
        for queue in tuple(self._sse_subscribers):
            if queue.full():
                with suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            with suppress(asyncio.QueueFull):
                queue.put_nowait(event)

    async def sse_events(self, max_queue: int = 100) -> AsyncIterator[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=max_queue)
        self._sse_subscribers.add(queue)
        try:
            while not self._closed:
                try:
                    yield await asyncio.wait_for(queue.get(), timeout=20)
                except TimeoutError:
                    yield {"type": "heartbeat"}
        finally:
            self._sse_subscribers.discard(queue)

    async def close(self) -> None:
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            tasks = tuple(self._tasks.values())
            self._tasks.clear()
            self._profile_pollers.clear()
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.publish_sse({"type": "closed"})
        self._sse_subscribers.clear()
