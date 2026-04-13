from __future__ import annotations

from overwatch.analysis.json_extract import parse_model_json
from overwatch.config import Settings
from overwatch.factorio.models import FactorioPlan, FactorioState, GameAction, GameActionType
from overwatch.factorio.skills import list_skills
from overwatch.vllm_client import chat_completion, extract_assistant_text


def _planner_instruction(*, allowed_skills: list[str]) -> str:
    skills = ", ".join(allowed_skills)
    return f"""You are a cautious Factorio play assistant. You only choose the next low-level UI action.

Player goal (high level): provided separately as user text.

Observed game state: JSON from a vision model (may be wrong).

Return ONE JSON object only (no markdown fences) with this shape:
- schema_version: "1"
- rationale: short string or null (why this action)
- action: object with:
  - type: one of "skill", "key", "keys", "noop"
  - skill: string or null — MUST be one of: {skills} (only when type is "skill")
  - key: string or null (pyautogui key name when type is "key")
  - keys: array of strings or null (when type is "keys")

Prefer "skill" with a named skill when it advances information (e.g. open research). Use "noop" if unsure or the UI is ambiguous.
Never invent skill names outside the list above.
"""


async def plan_next_action(
    settings: Settings,
    *,
    goal: str,
    state: FactorioState,
    tech_tree_text: str | None = None,
) -> tuple[FactorioPlan | None, str | None]:
    """
    Text-only vLLM call → :class:`FactorioPlan`.

    Reuses ``vllm_agent_max_tokens`` and ``vllm_agent_timeout_sec`` to avoid extra env knobs.

    Returns ``(plan, raw_assistant_text)``.
    """
    base = (settings.vllm_base_url or "").strip()
    if not base:
        return None, None

    user_parts = [f"Goal:\n{goal.strip()}\n", f"Observed state JSON:\n{state.model_dump_json()}\n"]
    if tech_tree_text:
        user_parts.append(f"Tech / milestone context:\n{tech_tree_text[:8000]}\n")

    messages = [
        {"role": "system", "content": _planner_instruction(allowed_skills=list_skills())},
        {"role": "user", "content": "".join(user_parts)},
    ]
    res = await chat_completion(
        base,
        model=settings.vllm_model,
        messages=messages,
        api_key=settings.vllm_api_key,
        timeout_sec=settings.vllm_agent_timeout_sec,
        max_tokens=settings.vllm_agent_max_tokens,
        temperature=0.2,
    )
    if not res.ok or not res.data:
        return None, None
    text = extract_assistant_text(res.data)
    plan = parse_model_json(text, FactorioPlan)
    if plan is None:
        return None, text
    # Normalize illegal skill to noop
    if plan.action.type == GameActionType.skill and plan.action.skill:
        allowed = set(list_skills())
        if plan.action.skill not in allowed:
            fixed = GameAction(type=GameActionType.noop)
            plan = FactorioPlan(
                schema_version="1",
                rationale=(plan.rationale or "") + " [sanitized: unknown skill]",
                action=fixed,
            )
    return plan, text
