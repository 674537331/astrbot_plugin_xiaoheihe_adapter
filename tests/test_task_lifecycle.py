from __future__ import annotations

import asyncio

import pytest

from xiaoheihe.task_manager import TaskManager


async def test_named_task_reuse_cancel_and_close() -> None:
    manager = TaskManager()
    gate = asyncio.Event()

    async def worker():
        await gate.wait()

    first = await manager.start("same", worker())
    second = await manager.start("same", worker())
    assert first is second
    await manager.cancel("same")
    assert "same" not in manager.task_names()
    await manager.close()
    await manager.close()
    with pytest.raises(RuntimeError, match="关闭"):
        await manager.start("late", worker())


async def test_sse_queue_is_bounded_and_closes() -> None:
    manager = TaskManager()
    iterator = manager.sse_events(max_queue=2)
    pending = asyncio.create_task(anext(iterator))
    await asyncio.sleep(0)
    manager.publish_sse({"type": "one"})
    assert (await pending)["type"] == "one"
    await iterator.aclose()
    await manager.close()


async def test_single_poller_per_profile() -> None:
    manager = TaskManager()
    gate = asyncio.Event()

    async def poller():
        await gate.wait()

    first = await manager.start_profile_poller("p", poller())
    second = await manager.start_profile_poller("p", poller())
    assert first is second
    await manager.close()


async def test_task_failure_is_consumed_and_reported() -> None:
    manager = TaskManager()

    async def broken():
        raise RuntimeError("Authorization: Bearer secret-token-value")

    task = await manager.start("broken", broken())
    await asyncio.gather(task, return_exceptions=True)
    await asyncio.sleep(0)
    failures = manager.failures()
    assert failures[0]["task"] == "broken"
    assert "secret-token-value" not in failures[0]["error"]
    await manager.close()
