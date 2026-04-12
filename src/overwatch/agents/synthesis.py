from __future__ import annotations

import json
import logging
from typing import Any

from overwatch.analysis.json_extract import parse_model_json
from overwatch.config import Settings
from overwatch.models import SynthesisAgentResult
from overwatch.vllm_client import chat_completion, extract_assistant_text

logger = logging.getLogger(__name__)

AGENT_SYNTHESIS_EVENT = "agent_synthesis"
AGENT_ID = "synthesis"

_MAX_SUMMARY_JSON_CHARS = 200_000

_SYSTEM = """You are an operations analyst for warehouse / CCTV video review.
You receive ONLY structured JSON: an Overwatch job summary with per-chunk scene descriptions, events, security, logistics, and anonymous attendance counts.
Produce a concise cross-chunk synthesis for human operators. Do not invent facts not supported by the input.
No personal identities. Output ONLY one JSON object (no markdown fences, no commentary)."""

_USER_TEMPLATE = """Input job summary (JSON). It may be truncated if very large; call that out mentally and stay conservative.

{payload}

Required JSON shape (all keys required; use empty strings or empty arrays if nothing applies):
{{
  "schema_version": "1",
  "executive_summary": "<2-5 sentences>",
  "key_observations": ["<bullet>", "..."],
  "security_highlights": ["<bullet>", "..."],
  "logistics_highlights": ["<bullet>", "..."],
  "attendance_summary": "<short paragraph; counts only>",
  "recommended_actions": ["<action>", "..."]
}}
"""


def _repair_message(invalid_snippet: str) -> dict[str, str]:
    return {
        "role": "user",
        "content": (
            "That response was not valid JSON for the required schema. "
            "Reply with ONLY one JSON object, no markdown fences, no commentary.\n\n"
            f"Invalid output (truncated):\n{invalid_snippet[:1500]}"
        ),
    }


def _prepare_summary_blob(summary: dict[str, Any]) -> tuple[str, bool]:
    raw = json.dumps(summary, ensure_ascii=False, indent=2)
    if len(raw) <= _MAX_SUMMARY_JSON_CHARS:
        return raw, False
    return raw[:_MAX_SUMMARY_JSON_CHARS] + "\n…", True


async def run_synthesis_agent(
    settings: Settings,
    summary: dict[str, Any],
) -> tuple[SynthesisAgentResult | None, dict[str, Any]]:
    """
    Text-only LLM pass over ``summary`` JSON.

    Returns ``(result | None, meta)`` where ``meta`` includes ``attempts``, ``truncated_input``, and optional ``error``.
    """
    base = settings.vllm_base_url.strip()
    if not base:
        return None, {"error": "VLLM_BASE_URL is not set", "attempts": 0, "truncated_input": False}

    blob, truncated = _prepare_summary_blob(summary)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": _USER_TEMPLATE.format(payload=blob)},
    ]

    retries = max(1, settings.vllm_json_retry_max)
    attempts = 0
    last_error: str | None = None

    for attempt in range(retries):
        attempts = attempt + 1
        res = await chat_completion(
            base,
            model=settings.vllm_model,
            messages=messages,
            api_key=settings.vllm_api_key,
            timeout_sec=settings.vllm_agent_timeout_sec,
            max_tokens=settings.vllm_agent_max_tokens,
            temperature=0.2,
        )
        text = extract_assistant_text(res.data)
        if not res.ok:
            last_error = res.error or "LLM request failed"
            logger.warning("Synthesis agent HTTP error: %s", last_error)
        parsed = parse_model_json(text, SynthesisAgentResult)
        if parsed is not None:
            return parsed, {
                "attempts": attempts,
                "truncated_input": truncated,
                "model": settings.vllm_model,
            }
        last_error = "Model output was not valid SynthesisAgentResult JSON"
        if attempt + 1 < retries:
            messages.append({"role": "assistant", "content": text or "(empty)"})
            messages.append(_repair_message(text or ""))

    return None, {
        "attempts": attempts,
        "truncated_input": truncated,
        "model": settings.vllm_model,
        "error": last_error,
    }
