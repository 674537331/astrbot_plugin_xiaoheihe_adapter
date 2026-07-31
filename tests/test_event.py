from __future__ import annotations

import time

from tests.astrbot_stubs import (
    AstrBotMessage,
    MessageChain,
    Plain,
    PlatformMetadata,
)
from xiaoheihe.event import XiaoheiheMessageEvent
from xiaoheihe.models import RoutingTarget


class FakeLogging:
    def __init__(self) -> None:
        self.entries = []

    def emit(self, *args, **kwargs) -> None:
        self.entries.append((args, kwargs))


class FakeRuntime:
    def __init__(self) -> None:
        self.deliveries = []
        self.candidates = []
        self.expired = []
        self.failures = []
        self.segmented_profiles = []
        self.logging = FakeLogging()

    async def deliver(self, **kwargs):
        self.deliveries.append(kwargs)

    async def capture_feed_candidate(self, **kwargs):
        self.candidates.append(kwargs)

    async def expire_reply(self, **kwargs):
        self.expired.append(kwargs)

    async def fail_reply(self, **kwargs):
        self.failures.append(kwargs)

    def report_segmented_reply_aggregated(self, profile_id):
        self.segmented_profiles.append(profile_id)


def make_event(runtime, *, candidate=False):
    message = AstrBotMessage()
    message.sender = type("Sender", (), {"user_id": "1", "nickname": "u"})()
    return XiaoheiheMessageEvent(
        "hello",
        message,
        PlatformMetadata("xiaoheihe", "x", "adapter"),
        "xhh_thread_1_2",
        runtime=runtime,
        route=RoutingTarget("default", "1", "2", "2"),
        event_id=1,
        dry_run=True,
        capture_candidate=candidate,
    )


async def test_once_only_send_and_parent_state() -> None:
    runtime = FakeRuntime()
    event = make_event(runtime)
    await event.send(MessageChain([Plain("hello")]))
    await event.send(MessageChain([Plain("second")]))
    assert len(runtime.deliveries) == 1
    assert runtime.deliveries[0]["generated_ms"] >= 0
    assert event.parent_send_count == 1
    assert runtime.logging.entries
    assert runtime.logging.entries[0][0][0] == "DEBUG"


async def test_streaming_is_aggregated_to_one_comment() -> None:
    runtime = FakeRuntime()
    event = make_event(runtime)

    async def chunks():
        yield MessageChain([Plain("a")])
        yield MessageChain([Plain("b")])

    await event.send_streaming(chunks())
    assert runtime.deliveries[0]["content"] == "ab"
    assert len(runtime.deliveries) == 1
    assert event.parent_send_count == 1
    assert event.parent_stream_count == 0


async def test_astrbot_segmented_reply_uses_complete_text_for_any_segment_count() -> None:
    runtime = FakeRuntime()
    event = make_event(runtime)
    complete_text = "第一段，保留逗号。\n\n第二段！第三段？最后一段。"
    event.set_extra("xiaoheihe_complete_reply_text", complete_text)
    event.get_result = lambda: type(
        "Result",
        (),
        {
            "chain": [
                Plain("第一段"),
                Plain("保留逗号"),
                Plain("第二段"),
                Plain("第三段"),
                Plain("最后一段"),
            ]
        },
    )()

    for segment in ("第一段", "保留逗号", "第二段", "第三段", "最后一段"):
        await event.send(MessageChain([Plain(segment)]))

    assert runtime.deliveries[0]["content"] == complete_text
    assert len(runtime.deliveries) == 1
    assert event.parent_send_count == 1
    assert runtime.segmented_profiles == ["default"]
    assert "完整结果覆盖" in runtime.logging.entries[0][0][1]


async def test_proactive_event_captures_candidate() -> None:
    runtime = FakeRuntime()
    event = make_event(runtime, candidate=True)
    await event.send(MessageChain([Plain("candidate")]))
    assert runtime.candidates[0]["content"] == "candidate"
    assert not runtime.deliveries


async def test_reply_after_deadline_is_suppressed() -> None:
    runtime = FakeRuntime()
    event = make_event(runtime)
    event.reply_timeout_seconds = 5
    event._started_at = time.perf_counter() - 6
    await event.send(MessageChain([Plain("late")]))
    assert not runtime.deliveries
    assert runtime.expired[0]["event_id"] == 1
    assert runtime.expired[0]["reply_timeout_seconds"] == 5


async def test_empty_final_reply_is_retryable_without_platform_send() -> None:
    runtime = FakeRuntime()
    event = make_event(runtime)
    await event.send(MessageChain([]))
    await event.wait_finished()
    assert not runtime.deliveries
    assert runtime.failures[0]["error"] == "AstrBot 原生管线返回空回复"
