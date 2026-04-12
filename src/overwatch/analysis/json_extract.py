from __future__ import annotations

import json
import re
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)


def first_json_object(text: str) -> dict[str, Any] | None:
    """Extract the first JSON object from model output (handles ```json fences)."""
    t = text.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", t, re.IGNORECASE)
    if fence:
        t = fence.group(1).strip()
    dec = json.JSONDecoder()
    i = t.find("{")
    while i >= 0:
        try:
            obj, _end = dec.raw_decode(t[i:])
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
        i = t.find("{", i + 1)
    return None


def parse_model_json(text: str | None, model: type[T]) -> T | None:
    if not text:
        return None
    raw = first_json_object(text)
    if raw is None:
        return None
    try:
        return model.model_validate(raw)
    except ValidationError:
        return None
