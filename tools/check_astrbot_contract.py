from __future__ import annotations

import argparse
import importlib.metadata
from pathlib import Path

CONTRACT: dict[str, tuple[str, ...]] = {
    "api/platform/__init__.py": (
        "AstrBotMessage",
        "MessageMember",
        "MessageType",
        "Platform",
        "PlatformMetadata",
        "register_platform_adapter",
    ),
    "api/event/__init__.py": ("AstrMessageEvent", "MessageChain"),
    "api/event/filter/__init__.py": (
        "on_agent_begin",
        "on_agent_done",
        "on_llm_request",
        "on_llm_response",
    ),
    "api/message_components.py": ("astrbot.core.message.components",),
    "core/message/components.py": ("class Plain", "class Image"),
    "api/provider/__init__.py": ("LLMResponse", "ProviderRequest"),
    "api/star/__init__.py": ("Context", "Star", "StarTools", "register"),
    "core/star/context.py": (
        "get_using_provider",
        "self.platform_manager = platform_manager",
    ),
    "core/star/star_manager.py": ("await metadata.star_cls.initialize()",),
    "core/platform/manager.py": ("async def reload(", "def get_insts("),
    "core/platform/astr_message_event.py": (
        "MessageSession",
        "AstrMessageEvent",
        "def get_result(",
    ),
    "core/agent/message.py": ("TextPart", "mark_as_temp"),
}
WEB_CONTRACT = {
    "api/web.py": ("request", "json_response", "error_response", "stream_response"),
}


def version_tuple(value: str) -> tuple[int, int, int]:
    core = value.split("-", 1)[0].split("+", 1)[0]
    parts = core.split(".")
    return tuple(int(part) for part in [*parts, "0", "0"][:3])


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the AstrBot API used by this plugin")
    parser.add_argument(
        "--expected-version",
        help="Exact version expected; omit when checking the latest installed stable package",
    )
    parser.add_argument(
        "--astrbot-root",
        type=Path,
        help="Optional extracted astrbot package directory for offline validation",
    )
    args = parser.parse_args()

    if args.astrbot_root is not None:
        if not args.expected_version:
            parser.error("--astrbot-root requires --expected-version")
        installed = args.expected_version
        root = args.astrbot_root.resolve()
    else:
        distribution = importlib.metadata.distribution("AstrBot")
        installed = distribution.version
        root = Path(distribution.locate_file("astrbot")).resolve()
    if args.expected_version and installed != args.expected_version:
        raise SystemExit(f"expected AstrBot {args.expected_version}, found {installed}")
    failures: list[str] = []
    contract = dict(CONTRACT)
    if version_tuple(installed) >= (4, 26, 2):
        contract.update(WEB_CONTRACT)
    for relative, symbols in contract.items():
        path = root / relative
        if not path.is_file():
            failures.append(f"missing file: astrbot/{relative}")
            continue
        source = path.read_text(encoding="utf-8")
        for symbol in symbols:
            if symbol not in source:
                failures.append(f"missing symbol text {symbol!r} in astrbot/{relative}")
    if failures:
        raise SystemExit("\n".join(failures))
    page_status = "with Plugin Page API" if WEB_CONTRACT.keys() <= contract.keys() else "core only"
    print(f"AstrBot {installed} contract OK ({len(contract)} files, {page_status})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
