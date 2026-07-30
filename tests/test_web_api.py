from __future__ import annotations

from pathlib import Path

from tests.astrbot_stubs import REQUEST, Query
from xiaoheihe.config_service import ConfigService
from xiaoheihe.web_api import WebApiController


class FakeContext:
    def __init__(self) -> None:
        self.routes = []

    def register_web_api(self, route, handler, methods, description):
        self.routes.append((route, handler, methods, description))


class FakeRuntime:
    def __init__(self, config) -> None:
        self.config = ConfigService(config)


class FakeApprovalRepository:
    async def feed_candidate(self, candidate_id: int):
        if candidate_id != 7:
            return None
        return {"id": 7, "profile_id": "default"}


class FakeApprovalFeed:
    def __init__(self) -> None:
        self.calls = []

    async def approve(self, candidate_id: int, edited_text: str | None) -> str:
        self.calls.append((candidate_id, edited_text))
        return "dry_run"


class FakeApprovalRuntime:
    def __init__(self) -> None:
        self.repository = FakeApprovalRepository()
        self.feed = FakeApprovalFeed()

    async def get_client(self, profile_id: str):
        assert profile_id == "default"
        return object()

    def feed_service(self, profile_id: str, client, synthetic_dispatch):
        assert profile_id == "default"
        assert client is not None
        assert synthetic_dispatch is not None
        return self.feed


def test_config_save_shows_success_toast() -> None:
    source = (Path(__file__).resolve().parents[1] / "pages" / "xiaoheihe" / "app.js").read_text(
        encoding="utf-8"
    )
    save_handler = source.split('"save-config"', maxsplit=1)[1].split(
        '"restore-defaults"', maxsplit=1
    )[0]
    assert 'toast(changed === "无变化"' in save_handler
    assert '"success"' in save_handler


def test_plugin_page_uses_embedded_confirmation_for_candidate_approval() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "pages" / "xiaoheihe" / "app.js").read_text(encoding="utf-8")
    html = (root / "pages" / "xiaoheihe" / "index.html").read_text(encoding="utf-8")
    approval_handler = source.split('approve.textContent = "批准"', maxsplit=1)[1].split(
        'reject.textContent = "拒绝"', maxsplit=1
    )[0]
    assert 'id="confirm-overlay"' in html
    assert "await confirmAction(" in approval_handler
    assert "window.confirm(" not in source
    assert "if (!confirm(" not in source
    assert 'result.result === "dry_run"' in approval_handler


def test_registers_only_plugin_prefixed_routes(fake_config) -> None:
    controller = WebApiController(FakeRuntime(fake_config))
    context = FakeContext()
    controller.register(context)
    assert len(context.routes) >= 15
    assert all(
        route.startswith("/astrbot_plugin_xiaoheihe_adapter/") for route, _, _, _ in context.routes
    )


async def test_config_api_reads_and_saves_same_object(fake_config) -> None:
    runtime = FakeRuntime(fake_config)
    controller = WebApiController(runtime)
    REQUEST.username = "admin"
    response = await controller.config_get()
    assert response["status_code"] == 200
    payload = response["json"]
    payload["reply"]["max_reply_chars"] = 321
    REQUEST._json = payload
    saved = await controller.config_save()
    assert saved["status_code"] == 200
    assert fake_config["reply"]["max_reply_chars"] == 321
    assert fake_config.save_count == 1


async def test_config_schema_api_exposes_structured_form_metadata(fake_config) -> None:
    controller = WebApiController(FakeRuntime(fake_config))
    REQUEST.username = "admin"
    response = await controller.config_schema()
    assert response["status_code"] == 200
    schema = response["json"]
    assert schema["profiles"]["type"] == "template_list"
    assert schema["polling"]["items"]["poll_interval_seconds"]["type"] == "int"
    assert schema["proactive_feed"]["items"]["section"]["options"][0] == "All（全部）"
    assert schema["proactive_feed"]["items"]["selection_strategy"]["options"] == [
        "推荐顺序",
        "随机",
        "最新",
        "热门",
    ]
    assert schema["logging"]["items"]["level"]["options"] == [
        "DEBUG",
        "INFO",
        "WARNING",
        "ERROR",
    ]


async def test_config_api_rejects_unknown_group(fake_config) -> None:
    controller = WebApiController(FakeRuntime(fake_config))
    REQUEST.username = "admin"
    REQUEST._json = {"unexpected": {}}
    response = await controller.config_save()
    assert response["status_code"] == 400


async def test_config_api_rejects_non_object_body(fake_config) -> None:
    controller = WebApiController(FakeRuntime(fake_config))
    REQUEST.username = "admin"
    REQUEST._json = []
    response = await controller.config_save()
    assert response["status_code"] == 400


async def test_web_api_requires_dashboard_identity(fake_config) -> None:
    controller = WebApiController(FakeRuntime(fake_config))
    REQUEST.username = None
    response = await controller.config_get()
    assert response["status_code"] == 401
    REQUEST.username = "admin"
    REQUEST.query = Query({})


async def test_feed_approve_api_passes_edited_text_to_feed_service() -> None:
    runtime = FakeApprovalRuntime()
    controller = WebApiController(runtime)
    REQUEST.username = "admin"
    REQUEST._json = {"edited_text": "  审核后的回复  "}
    response = await controller.feed_approve("7")
    assert response["status_code"] == 200
    assert response["json"] == {"approved": True, "result": "dry_run"}
    assert runtime.feed.calls == [(7, "审核后的回复")]
