from __future__ import annotations

import json
from pathlib import Path


def load_fixture(name: str):
    path = Path(__file__).parent / "fixtures" / name
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)
