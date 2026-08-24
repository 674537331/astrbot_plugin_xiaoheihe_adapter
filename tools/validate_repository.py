from __future__ import annotations

import json
import re
import sys
import tomllib
from html.parser import HTMLParser
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED = {
    ".git",
    ".pytest_cache",
    ".ruff_cache",
    "work",
    "outputs",
    "__pycache__",
    "build",
    "dist",
}
EXCLUDED_FILES = {".coverage", "coverage.xml"}
EXCLUDED_PREFIXES = (".pycache-", ".test-")
FORBIDDEN_SUFFIXES = {".db", ".db-shm", ".db-wal", ".log", ".qr"}
TEXT_SUFFIXES = {".css", ".html", ".js", ".json", ".md", ".py", ".toml", ".txt", ".yaml", ".yml"}
MAX_ARCHIVE_INPUT_BYTES = 5 * 1024 * 1024


class BasicHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.has_title = False
        self.has_module_script = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "title":
            self.has_title = True
        if tag == "script" and values.get("type") == "module":
            self.has_module_script = True


def files() -> list[Path]:
    return [
        path
        for path in ROOT.rglob("*")
        if path.is_file()
        and path.name not in EXCLUDED_FILES
        and not any(
            part in EXCLUDED
            or part == ".codex-remote-attachments"
            or part.startswith(EXCLUDED_PREFIXES)
            or part.endswith(".egg-info")
            for part in path.relative_to(ROOT).parts
        )
    ]


def _release_version_failures() -> list[str]:
    failures: list[str] = []
    metadata = yaml.safe_load((ROOT / "metadata.yaml").read_text(encoding="utf-8")) or {}
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    package_source = (ROOT / "xiaoheihe" / "__init__.py").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

    package_match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', package_source, re.M)
    changelog_match = re.search(r"^## v([^\s]+)\s+-\s+\d{4}-\d{2}-\d{2}\s*$", changelog, re.M)
    versions = {
        "metadata.yaml": str(metadata.get("version", "")).removeprefix("v"),
        "pyproject.toml": str(pyproject.get("project", {}).get("version", "")),
        "xiaoheihe.__version__": package_match.group(1) if package_match else "",
        "CHANGELOG.md": changelog_match.group(1) if changelog_match else "",
    }
    expected = versions["xiaoheihe.__version__"]
    if not expected:
        failures.append("xiaoheihe/__init__.py: missing __version__")
        return failures
    for label, value in versions.items():
        if value != expected:
            failures.append(f"release version mismatch: {label}={value!r}, expected {expected!r}")
    if f"当前版本：**v{expected}**" not in readme:
        failures.append(f"README.md: current version must be v{expected}")

    bug_template = (ROOT / ".github" / "ISSUE_TEMPLATE" / "bug_report.yml").read_text(
        encoding="utf-8"
    )
    if f"value: v{expected}" not in bug_template:
        failures.append(f"bug_report.yml: plugin version default must be v{expected}")

    web_api = (ROOT / "xiaoheihe" / "web_api.py").read_text(encoding="utf-8")
    stale_literal = re.search(r'"version"\s*:\s*"v\d+\.\d+\.\d+"', web_api)
    if stale_literal:
        failures.append("xiaoheihe/web_api.py: diagnostics version must use package __version__")

    docs_markers = {
        "docs/architecture.md": f"# 架构说明（v{expected}）",
        "docs/compatibility.md": f"# AstrBot 兼容性说明（v{expected}）",
        "docs/testing.md": f"# 测试说明（v{expected}）",
        "docs/xiaoheihe-api-contract.md": f"# 小黑盒 API 契约与验证状态（v{expected}）",
    }
    for relative, marker in docs_markers.items():
        if marker not in (ROOT / relative).read_text(encoding="utf-8"):
            failures.append(f"{relative}: current-version marker must be v{expected}")

    version_parts = expected.split(".")
    supported_series = ".".join(version_parts[:2]) if len(version_parts) >= 2 else expected
    security = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
    if f"当前维护版本为 **v{supported_series}.x**" not in security:
        failures.append(f"SECURITY.md: supported series must be v{supported_series}.x")
    return failures


def _schema_failures() -> list[str]:
    failures: list[str] = []
    schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
    proactive = schema.get("proactive_feed", {}).get("items", {})
    expected = {
        "source": "all",
        "topic_ids": [],
        "fallback_sources": ["all"],
    }
    for field, default in expected.items():
        item = proactive.get(field)
        if not isinstance(item, dict):
            failures.append(f"_conf_schema.json: missing proactive_feed.{field}")
            continue
        if item.get("invisible") is not True:
            failures.append(f"_conf_schema.json: proactive_feed.{field} must be invisible")
        if item.get("default") != default:
            failures.append(
                f"_conf_schema.json: proactive_feed.{field} default must be {default!r}"
            )
    return failures


def main() -> int:
    failures: list[str] = []
    repository_files = files()
    for path in repository_files:
        relative = path.relative_to(ROOT).as_posix()
        try:
            source = (
                path.read_text(encoding="utf-8") if path.suffix.lower() in TEXT_SUFFIXES else ""
            )
            if path.suffix == ".json":
                json.loads(source)
            elif path.suffix in {".yaml", ".yml"}:
                yaml.safe_load(source)
        except (UnicodeError, json.JSONDecodeError, yaml.YAMLError) as exc:
            failures.append(f"{relative}: invalid syntax: {exc}")
            source = ""
        if "\ufffd" in source or "?" * 3 in source:
            failures.append(f"{relative}: suspicious text-encoding corruption marker")

        lower_name = path.name.lower()
        if any(lower_name.endswith(suffix) for suffix in FORBIDDEN_SUFFIXES):
            failures.append(f"{relative}: forbidden runtime artifact")
        if "credential" in {part.lower() for part in path.relative_to(ROOT).parts}:
            failures.append(f"{relative}: credentials directory must not be committed")

    html_path = ROOT / "pages" / "xiaoheihe" / "index.html"
    parser = BasicHtmlParser()
    parser.feed(html_path.read_text(encoding="utf-8"))
    if not parser.has_title or not parser.has_module_script:
        failures.append("pages/xiaoheihe/index.html: missing title or module script")

    app_source = (ROOT / "pages" / "xiaoheihe" / "app.js").read_text(encoding="utf-8")
    for required in (
        "window.AstrBotPluginPage",
        "bridge.ready()",
        "bridge.subscribeSSE",
        'bridge.apiGet("config/schema")',
        "renderConfigForm",
        'toast(changed === "无变化"',
    ):
        if required not in app_source:
            failures.append(f"pages/xiaoheihe/app.js: missing {required}")
    html_source = html_path.read_text(encoding="utf-8")
    if 'id="config-form"' not in html_source:
        failures.append("pages/xiaoheihe/index.html: missing structured config form")
    if 'data-tab="sources"' not in html_source:
        failures.append("pages/xiaoheihe/index.html: missing browse sources tab")
    for forbidden in ("document.cookie", "localStorage", "window.parent", "parent.document"):
        if forbidden in app_source:
            failures.append(f"pages/xiaoheihe/app.js: forbidden browser access {forbidden}")

    failures.extend(_release_version_failures())
    failures.extend(_schema_failures())

    package_bytes = sum(
        path.stat().st_size
        for path in repository_files
        if path.relative_to(ROOT).parts[0] not in {"tests", "tools", ".github"}
    )
    if package_bytes > MAX_ARCHIVE_INPUT_BYTES:
        failures.append(
            f"plugin source size {package_bytes} exceeds {MAX_ARCHIVE_INPUT_BYTES} bytes"
        )

    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    print(f"repository validation OK: {len(repository_files)} files, {package_bytes} plugin bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
