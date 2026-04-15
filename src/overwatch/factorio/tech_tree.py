from __future__ import annotations

import json
from pathlib import Path


def load_tech_tree_text(path: Path | None) -> str | None:
    """Load a JSON (or plain text) file for planner grounding; returns None if path is missing."""
    if path is None:
        return None
    p = Path(path)
    if not p.is_file():
        return None
    raw = p.read_text(encoding="utf-8")
    try:
        obj = json.loads(raw)
        return json.dumps(obj, indent=2)[:8000]
    except json.JSONDecodeError:
        return raw[:8000]
