from __future__ import annotations

from pathlib import Path

path = Path("main.py")
text = path.read_text(encoding="utf-8")
old = '''        if bool(event.get_extra(EARLY_THREAD_COMPRESSION_ATTEMPTED_EXTRA, False)):\n            return None\n        self._set_event_extra(event, EARLY_THREAD_COMPRESSION_ATTEMPTED_EXTRA, True)\n'''
new = '''        get_extra = getattr(event, "get_extra", None)\n        if callable(get_extra) and bool(\n            get_extra(EARLY_THREAD_COMPRESSION_ATTEMPTED_EXTRA, False)\n        ):\n            return None\n        self._set_event_extra(event, EARLY_THREAD_COMPRESSION_ATTEMPTED_EXTRA, True)\n'''
count = text.count(old)
if count != 1:
    raise SystemExit(f"main.py: expected one compression guard to harden, got {count}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
