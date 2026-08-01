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


def test_plugin_page_defers_hidden_panel_requests_and_log_stream() -> None:
    source = (Path(__file__).resolve().parents[1] / "pages" / "xiaoheihe" / "app.js").read_text(
        encoding="utf-8"
    )
    initial_load = source.split("async function initialLoad()", maxsplit=1)[1].split(
        "document.querySelectorAll", maxsplit=1
    )[0]
    assert "loadConfig()" in initial_load
    assert "loadStatus()" in initial_load
    for deferred in (
        "loadLogin()",
        "loadEvents()",
        "loadCandidates()",
        "loadLogs()",
        "loadStorage()",
    ):
        assert deferred not in initial_load
    assert "connectSse()" not in initial_load
    assert 'activeTab() !== "logs"' in source
    assert "queueLogRender()" in source


def test_plugin_page_localizes_events_and_formats_times() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "pages" / "xiaoheihe" / "app.js").read_text(encoding="utf-8")
    html = (root / "pages" / "xiaoheihe" / "index.html").read_text(encoding="utf-8")

    assert 'proactive_feed: "主动 AI 回复"' in source
    assert 'send_unknown: "发送状态未知"' in source
    assert 'timeZone: "Asia/Shanghai"' in source
    assert "formatTime(first.last_poll_at)" in source
    assert "formatTime(result.last_cleanup_at)" in source
    assert "formatTime(entry.time)" in source
    assert "toLocaleString()" not in source
    assert '<option value="dead_letter">处理失败</option>' in html


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
    assert schema["providers"]["items"]["llm_provider_id"]["_special"] == ("select_provider")
    assert schema["providers"]["items"]["image_provider_id"]["_special"] == ("select_provider")
    assert schema["logging"]["items"]["level"]["options"] == [
        "DEBUG",
        "INFO",
        "WARNING",
        "ERROR",
    ]


async def test_config_schema_includes_configured_provider_choices(fake_config) -> None:
    controller = WebApiController(
        FakeRuntime(fake_config),
        provider_supplier=lambda: [
            {"value": "provider-a", "label": "provider-a · test-model"},
            {"value": "provider-b", "label": "provider-b"},
        ],
    )
    REQUEST.username = "admin"
    schema = (await controller.config_schema())["json"]
    options = schema["providers"]["items"]["llm_provider_id"]["options"]
    assert options == [
        {"value": "", "label": "跟随当前配置"},
        {"value": "provider-a", "label": "provider-a · test-model"},
        {"value": "provider-b", "label": "provider-b"},
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
