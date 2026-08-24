from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected exactly one match for {old!r}, got {count}")
    file_path.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "tests/fixtures/approval_page_harness.html",
    'version: "v1.3.2"',
    'version: "v1.3.3"',
)
replace_once(
    "tests/fixtures/approval_page_harness.html",
    "index.html?v=1.3.2",
    "index.html?v=1.3.3",
)
replace_once("docs/architecture.md", "# 架构说明（v1.3.2）", "# 架构说明（v1.3.3）")
replace_once(
    "docs/compatibility.md",
    "# AstrBot 兼容性说明（v1.3.2）",
    "# AstrBot 兼容性说明（v1.3.3）",
)
replace_once("docs/testing.md", "# 测试说明（v1.3.2）", "# 测试说明（v1.3.3）")
replace_once(
    "docs/xiaoheihe-api-contract.md",
    "# 小黑盒 API 契约与验证状态（v1.3.2）",
    "# 小黑盒 API 契约与验证状态（v1.3.3）",
)
