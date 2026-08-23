from __future__ import annotations

from pathlib import Path

from tests.astrbot_stubs import REQUEST, Query
from xiaoheihe import web_api as web_module
from xiaoheihe.config_service import ConfigService
from xiaoheihe.models import ApiPage
from xiaoheihe.web_api import WebApiController


class ProbeRuntime:
    def __init__(self, config) -> None:
        self.config = ConfigService(config)
        self.client = object()
        self.started = False

    async def ensure_started(self) -> None:
        self.started = True

    async def get_client(self, profile_id: str):
        assert profile_id == "default"
        return self.client


async def test_topic_probe_is_get_only_and_returns_catalog_plus_sample(
    fake_config,
    monkeypatch,
) -> None:
    runtime = ProbeRuntime(fake_config)
    controller = WebApiController(runtime)
    REQUEST.username = "admin"
    REQUEST.query = Query({"profile_id": "default"})
    calls = []

    async def fake_catalog(client):
        assert client is runtime.client
        calls.append("catalog")
        return [
            {"id": "100", "name": "PC 游戏", "group": "游戏"},
            {"id": "200", "name": "盒友杂谈", "group": "社区"},
        ]

    async def fake_feed(client, topic_id, *, limit=10, **kwargs):
        assert client is runtime.client
        calls.append(("feed", topic_id, limit))
        return ApiPage(
            items=[
                {
                    "post_id": "987",
                    "title": "探测样例",
                    "created_at": 1_700_000_000,
                }
            ]
        )

    monkeypatch.setattr(web_module, "fetch_topic_catalog", fake_catalog)
    monkeypatch.setattr(web_module, "fetch_topic_feed", fake_feed)

    response = await controller.feed_topics_probe()

    assert response["status_code"] == 200
    payload = response["json"]
    assert payload["read_only"] is True
    assert payload["topic_count"] == 2
    assert payload["feed_verified"] is True
    assert payload["verified_topic_id"] == "100"
    assert payload["sample_posts"][0]["post_id"] == "987"
    assert calls == ["catalog", ("feed", "100", 3)]


async def test_topic_probe_keeps_catalog_when_sample_feed_fails(fake_config, monkeypatch) -> None:
    runtime = ProbeRuntime(fake_config)
    controller = WebApiController(runtime)
    REQUEST.username = "admin"
    REQUEST.query = Query({"profile_id": "default"})

    async def fake_catalog(client):
        return [{"id": "100", "name": "PC 游戏", "group": "游戏"}]

    async def fake_feed(client, topic_id, **kwargs):
        raise RuntimeError("temporary feed error")

    monkeypatch.setattr(web_module, "fetch_topic_catalog", fake_catalog)
    monkeypatch.setattr(web_module, "fetch_topic_feed", fake_feed)

    response = await controller.feed_topics_probe()

    assert response["status_code"] == 200
    assert response["json"]["topics"][0]["id"] == "100"
    assert response["json"]["feed_verified"] is False
    assert "temporary feed error" in response["json"]["feed_error"]


def test_management_page_exposes_real_topic_probe_and_multiselect() -> None:
    root = Path(__file__).resolve().parents[1]
    html = (root / "pages" / "xiaoheihe" / "index.html").read_text(encoding="utf-8")
    source = (root / "pages" / "xiaoheihe" / "topic_probe.js").read_text(encoding="utf-8")

    assert 'id="probe-topics"' in html
    assert 'id="save-topic-selection"' in html
    assert 'src="./topic_probe.js"' in html
    assert 'bridge.apiGet("feed/topics/probe"' in source
    assert "config.proactive_feed.topic_ids = selected" in source
    assert "MAX_SELECTED_TOPICS = 20" in source
