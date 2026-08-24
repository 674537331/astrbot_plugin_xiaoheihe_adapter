from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise RuntimeError(f"{path}: expected one match for {old!r}")
    file_path.write_text(text.replace(old, new, 1), encoding="utf-8")


path = Path("tests/test_main_contract.py")
text = path.read_text(encoding="utf-8")
marker = "async def test_v133_long_post_short_floor_does_not_force_context_llm"
if marker not in text:
    raise RuntimeError("generated regression test marker not found")

before, tail = text.split(marker, 1)
old_state = (
    "        extras = {}\n\n"
    "        @classmethod\n"
    '        def get_extra(cls, key, default=""):\n'
    "            return cls.extras.get(key, default)\n\n"
    "        @classmethod\n"
    "        def set_extra(cls, key, value):\n"
    "            cls.extras[key] = value\n"
)
new_state = (
    "        def __init__(self) -> None:\n"
    "            self.extras = {}\n\n"
    '        def get_extra(self, key, default=""):\n'
    "            return self.extras.get(key, default)\n\n"
    "        def set_extra(self, key, value):\n"
    "            self.extras[key] = value\n"
)
if old_state not in tail:
    raise RuntimeError("generated Event state pattern not found")
tail = tail.replace(old_state, new_state, 1)

old_call = "    result = await plugin._compress_thread_context(\n        Event(),\n"
new_call = "    event = Event()\n    result = await plugin._compress_thread_context(\n        event,\n"
if old_call not in tail:
    raise RuntimeError("generated Event call pattern not found")
tail = tail.replace(old_call, new_call, 1)

tail = tail.replace(
    "assert Event.extras.get(module.EARLY_THREAD_COMPRESSION_ATTEMPTED_EXTRA) is None",
    "assert event.extras.get(module.EARLY_THREAD_COMPRESSION_ATTEMPTED_EXTRA) is None",
    1,
)
path.write_text(before + marker + tail, encoding="utf-8")

# Existing contracts previously required the main model to see the internal
# drift label. v1.3.3 final routing intentionally keeps that signal internal.
replace_once(
    "tests/test_context_compression.py",
    '    assert "已明显偏离原帖" in rendered\n',
    '    assert "已明显偏离原帖" not in rendered\n',
)
replace_once(
    "tests/test_main_contract.py",
    '    assert "本轮省略原帖文字和原帖图片摘要" in compressed\n',
    '    assert "本轮省略原帖文字和原帖图片摘要" not in compressed\n'
    '    assert "已明显偏离原帖" not in compressed\n',
)
print("generated regression fixture and legacy drift contracts normalized")
