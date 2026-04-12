from __future__ import annotations

import json
import logging
from typing import Any

from overwatch.analysis.json_extract import parse_model_json
from overwatch.config import Settings
from overwatch.models import RiskReviewAgentResult
from overwatch.vllm_client import chat_completion, extract_assistant_text

logger = logging.getLogger(__name__)

AGENT_RISK_REVIEW_EVENT = "agent_risk_review"
AGENT_ID = "risk_review"

_MAX_SUMMARY_JSON_CHARS = 200_000

_SYSTEM = """You are a safety and security analyst reviewing structured CCTV / warehouse video analysis JSON.
You only see aggregated chunk-level signals (scene text, security items, logistics, anonymous attendance counts).
Assess operational and safety risk for a human supervisor. Do not invent incidents not supported by the input.
No personal identities. Output ONLY one JSON object (no markdown fences, no commentary)."""

_USER_TEMPLATE = """Job summary (JSON). It may be truncated if very large; stay conservative.

{payload}

Required JSON shape (all keys required):
{{
  "schema_version": "1",
  "overall_risk": "low" | "medium" | "high" | "unknown",
  "requires_immediate_review": <true|false>,
  "risk_factors": ["<short bullet>", "..."],
  "operator_notes": "<2-4 sentences>",
  "mitigations_suggested": ["<action>", "..."]
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


async def run_risk_review_agent(
    settings: Settings,
    summary: dict[str, Any],
) -> tuple[RiskReviewAgentResult | None, dict[str, Any]]:
    """
    Text-only LLM risk triage over ``summary`` JSON.

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
            temperature=0.15,
        )
        text = extract_assistant_text(res.data)
        if not res.ok:
            last_error = res.error or "LLM request failed"
            logger.warning("Risk review agent HTTP error: %s", last_error)
        parsed = parse_model_json(text, RiskReviewAgentResult)
        if parsed is not None:
            return parsed, {
                "attempts": attempts,
                "truncated_input": truncated,
                "model": settings.vllm_model,
            }
        last_error = "Model output was not valid RiskReviewAgentResult JSON"
        if attempt + 1 < retries:
            messages.append({"role": "assistant", "content": text or "(empty)"})
            messages.append(_repair_message(text or ""))

    return None, {
        "attempts": attempts,
        "truncated_input": truncated,
        "model": settings.vllm_model,
        "error": last_error,
    }
