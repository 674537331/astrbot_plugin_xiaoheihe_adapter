from __future__ import annotations

import json
import sys
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
FORBIDDEN_SUFFIXES = {".db", ".db-shm", ".db-wal", ".log", ".qr"}
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
            part in EXCLUDED or part.endswith(".egg-info") for part in path.relative_to(ROOT).parts
        )
    ]


def main() -> int:
    failures: list[str] = []
    repository_files = files()
    for path in repository_files:
        relative = path.relative_to(ROOT).as_posix()
        try:
            if path.suffix == ".json":
                json.loads(path.read_text(encoding="utf-8"))
            elif path.suffix in {".yaml", ".yml"}:
                yaml.safe_load(path.read_text(encoding="utf-8"))
        except (UnicodeError, json.JSONDecodeError, yaml.YAMLError) as exc:
            failures.append(f"{relative}: invalid syntax: {exc}")

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
        "confirmAction",
        'toast(changed === "无变化"',
    ):
        if required not in app_source:
            failures.append(f"pages/xiaoheihe/app.js: missing {required}")
    if 'id="config-form"' not in html_path.read_text(encoding="utf-8"):
        failures.append("pages/xiaoheihe/index.html: missing structured config form")
    if 'id="confirm-overlay"' not in html_path.read_text(encoding="utf-8"):
        failures.append("pages/xiaoheihe/index.html: missing embedded confirmation dialog")
    if "if (!confirm(" in app_source or "window.confirm(" in app_source:
        failures.append("pages/xiaoheihe/app.js: native confirm is unreliable in plugin iframe")
    for forbidden in ("document.cookie", "localStorage", "window.parent", "parent.document"):
        if forbidden in app_source:
            failures.append(f"pages/xiaoheihe/app.js: forbidden browser access {forbidden}")

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
