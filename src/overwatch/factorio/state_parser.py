from __future__ import annotations

from overwatch.analysis.json_extract import parse_model_json
from overwatch.config import Settings
from overwatch.factorio.models import FactorioState
from overwatch.vllm_client import chat_completion, extract_assistant_text, image_png_user_messages

FACTORIO_STATE_INSTRUCTION = """You are parsing a screenshot from the game Factorio (or a similar factory UI).

Return ONE JSON object only (no markdown fences) matching this schema:
- schema_version: "1"
- tick_or_time_text: string or null (visible tick or playtime if readable)
- score_text: string or null (score or flow statistics if visible)
- researched_technologies: array of short strings (technologies clearly completed or visible in a tech UI)
- inventory_highlights: array of short strings (notable items/counts if visible)
- active_gui: string or null (e.g. "none", "research", "inventory", "production_stats", "map", "unknown")
- confidence: number 0..1 (how sure you are)
- raw_notes: string or null (brief caveats)

If the image is blank or not the game, set confidence low and use nulls/empty arrays where appropriate.
"""


async def parse_factorio_state_from_png(
    settings: Settings,
    png_bytes: bytes,
    *,
    tech_tree_context: str | None = None,
) -> tuple[FactorioState | None, str | None]:
    """
    Call vLLM with PNG + instruction; parse assistant output into :class:`FactorioState`.

    Returns ``(state, raw_assistant_text)``. ``state`` is None if vLLM is disabled, the call fails,
    or JSON does not validate.
    """
    base = (settings.vllm_base_url or "").strip()
    if not base:
        return None, None

    extra = ""
    if tech_tree_context:
        extra = "\n\nStatic tech tree / milestone context (may not match save):\n" + tech_tree_context[
            :8000
        ]

    messages = image_png_user_messages(
        instruction=FACTORIO_STATE_INSTRUCTION + extra,
        png_bytes=png_bytes,
    )
    res = await chat_completion(
        base,
        model=settings.vllm_model,
        messages=messages,
        api_key=settings.vllm_api_key,
        timeout_sec=settings.vllm_factorio_timeout_sec,
        max_tokens=settings.vllm_factorio_max_tokens,
        temperature=0.1,
    )
    if not res.ok or not res.data:
        return None, None
    text = extract_assistant_text(res.data)
    state = parse_model_json(text, FactorioState)
    return state, text
