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


def make_event(runtime, *, candidate=False, dry_run=True, proactive=False):
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
        dry_run=dry_run,
        proactive=proactive,
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


async def test_agent_without_tools_delivers_the_final_reply() -> None:
    runtime = FakeRuntime()
    event = make_event(runtime)
    event.mark_agent_started()
    event.mark_agent_done("普通模型最终回复")

    await event.send(MessageChain([Plain("普通模型最终回复")]))

    assert [item["content"] for item in runtime.deliveries] == ["普通模型最终回复"]
    assert event.parent_send_count == 1


async def test_tool_status_and_agent_intermediate_text_wait_for_final_reply() -> None:
    runtime = FakeRuntime()
    event = make_event(runtime)
    event.mark_agent_started()

    await event.send(MessageChain([Plain("🔨 调用工具: grok_web_search")], type="tool_call"))
    await event.send(MessageChain([Plain("我先查询一下")]))

    assert not runtime.deliveries
    assert not event._completion.is_set()

    event.mark_agent_done("这是最终搜索结果")
    await event.send(MessageChain([Plain("这是最终搜索结果")]))

    assert [item["content"] for item in runtime.deliveries] == ["这是最终搜索结果"]
    assert event.parent_send_count == 1


async def test_agent_done_hook_output_cannot_preempt_the_final_response() -> None:
    runtime = FakeRuntime()
    event = make_event(runtime)
    event.mark_agent_started()
    event.mark_agent_done("最终回答")

    await event.send(MessageChain([Plain("完成后的附加提示")]))
    assert not runtime.deliveries

    await event.send(MessageChain([Plain("最终回答")]))

    assert runtime.deliveries[0]["content"] == "最终回答\n\n完成后的附加提示"
    assert len(runtime.deliveries) == 1


async def test_direct_plugin_result_waits_when_the_event_will_call_llm() -> None:
    runtime = FakeRuntime()
    event = make_event(runtime)
    event.should_call_llm(True)

    await event.send(MessageChain([Plain("Grok 原始搜索结果")]))
    assert not runtime.deliveries

    event.mark_agent_started()
    event.mark_agent_done("模型整理后的结论")
    await event.send(MessageChain([Plain("模型整理后的结论")]))

    assert runtime.deliveries[0]["content"] == ("模型整理后的结论\n\nGrok 原始搜索结果")
    assert len(runtime.deliveries) == 1


async def test_direct_plugin_result_is_deduplicated_against_final_reply() -> None:
    runtime = FakeRuntime()
    event = make_event(runtime)
    event.should_call_llm(True)

    await event.send(MessageChain([Plain("搜索结果正文")]))
    event.mark_agent_done("根据检索，搜索结果正文")
    await event.send(MessageChain([Plain("根据检索，搜索结果正文")]))

    assert runtime.deliveries[0]["content"] == "根据检索，搜索结果正文"
    assert len(runtime.deliveries) == 1


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


async def test_unreviewed_proactive_event_uses_real_proactive_delivery() -> None:
    runtime = FakeRuntime()
    event = make_event(runtime, dry_run=False, proactive=True)
    await event.send(MessageChain([Plain("direct")]))
    assert not runtime.candidates
    assert runtime.deliveries[0]["dry_run"] is False
    assert runtime.deliveries[0]["proactive"] is True


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
