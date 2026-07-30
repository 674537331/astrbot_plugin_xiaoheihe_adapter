from __future__ import annotations

import asyncio
from pathlib import Path

from tests.astrbot_stubs import (
    REGISTERED_ADAPTERS,
    MessageChain,
    MessageSession,
    MessageType,
    Plain,
)
from xiaoheihe.adapter import XiaoheihePlatformAdapter
from xiaoheihe.runtime import bind_runtime, unbind_runtime


class FakeConfigService:
    def profile(self, profile_id):
        return {"profile_id": profile_id, "dry_run": True}


class FakeRuntime:
    def __init__(self) -> None:
        self.config = FakeConfigService()
        self.deliveries = []

    async def deliver(self, **kwargs):
        self.deliveries.append(kwargs)


def test_adapter_is_registered_and_nonstreaming() -> None:
    assert REGISTERED_ADAPTERS["xiaoheihe"] is XiaoheihePlatformAdapter
    registration = XiaoheihePlatformAdapter._test_registration
    assert registration["support_streaming_message"] is False
    assert registration["default_config_tmpl"]["profile_id"] == "default"
    assert registration["logo_path"] == "../logo.png"
    assert (Path("xiaoheihe") / registration["logo_path"]).resolve() == Path("logo.png").resolve()
    assert (Path("xiaoheihe") / registration["logo_path"]).is_file()


async def test_send_by_session_recovers_exact_route() -> None:
    runtime = FakeRuntime()
    bind_runtime(runtime)
    adapter = XiaoheihePlatformAdapter(
        {"id": "xhh-1", "profile_id": "default"},
        {},
        asyncio.Queue(),
    )
    session = MessageSession(
        platform_name="xhh-1",
        message_type=MessageType.GROUP_MESSAGE,
        session_id="xhh_thread_123_456",
    )
    await adapter.send_by_session(session, MessageChain([Plain("hello")]))
    route = runtime.deliveries[0]["route"]
    assert route.post_id == "123"
    assert route.root_comment_id == "456"
    assert adapter.super_send_count == 1
    unbind_runtime(runtime)


def test_adapter_meta_uses_instance_id() -> None:
    adapter = XiaoheihePlatformAdapter(
        {"id": "instance-a", "profile_id": "default"},
        {},
        asyncio.Queue(),
    )
    assert adapter.meta().name == "xiaoheihe"
    assert adapter.meta().id == "instance-a"
    assert adapter.meta().logo_path == "../logo.png"
    assert adapter.meta().support_streaming_message is False
