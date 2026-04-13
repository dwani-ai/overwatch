from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class FactorioState(BaseModel):
    """
    Structured HUD / menu readout from a screenshot (VLM output, validated).
    Field names are intentionally generic so the same schema can apply to similar UIs.
    """

    model_config = ConfigDict(extra="ignore")

    schema_version: Literal["1"] = "1"
    tick_or_time_text: str | None = None
    score_text: str | None = None
    researched_technologies: list[str] = Field(default_factory=list)
    inventory_highlights: list[str] = Field(default_factory=list)
    active_gui: str | None = None
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    raw_notes: str | None = None


class GameActionType(str, Enum):
    skill = "skill"
    key = "key"
    keys = "keys"
    noop = "noop"


class GameAction(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: GameActionType
    skill: str | None = None
    """Registered skill name from ``SKILL_KEY_SEQUENCES`` when ``type`` is ``skill``."""

    key: str | None = None
    """Single key name for pyautogui (e.g. ``escape``) when ``type`` is ``key``."""

    keys: list[str] | None = None
    """Sequence of key names when ``type`` is ``keys``."""


class FactorioPlan(BaseModel):
    """Planner output: one structured action plus optional reasoning (logged, not executed)."""

    model_config = ConfigDict(extra="ignore")

    schema_version: Literal["1"] = "1"
    rationale: str | None = None
    action: GameAction
