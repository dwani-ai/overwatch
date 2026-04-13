from __future__ import annotations

import json
import logging
from typing import Any

from overwatch.analysis.json_extract import parse_model_json
from overwatch.config import Settings
from overwatch.models import PrivacyReviewAgentResult
from overwatch.vllm_client import chat_completion, extract_assistant_text

logger = logging.getLogger(__name__)

AGENT_PRIVACY_REVIEW_EVENT = "agent_privacy_review"
AGENT_ID = "privacy_review"

_MAX_SUMMARY_JSON_CHARS = 200_000

_SYSTEM = """You are a privacy reviewer for video-analytics **structured JSON** (scene text, events, counts).
Flag risks of **re-identification**, unnecessary sensitive attributes, or wording that could enable discrimination
**only if such content appears in the input**. Do not hallucinate PII. Suggest safer reporting patterns.
No personal identities in your output. Output ONLY one JSON object (no markdown fences, no commentary)."""

_USER_TEMPLATE = """Job summary (JSON). May be truncated; note truncation in your assessment if relevant.

{payload}

Required JSON shape (all keys required):
{{
  "schema_version": "1",
  "overall_privacy_risk": "low" | "medium" | "high" | "unknown",
  "identity_inference_risks": ["<bullet>", "..."],
  "sensitive_descriptors": ["<bullet>", "..."],
  "safe_output_guidance": ["<bullet>", "..."],
  "summary": "<2-4 sentences>"
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


async def run_privacy_review_agent(
    settings: Settings,
    summary: dict[str, Any],
) -> tuple[PrivacyReviewAgentResult | None, dict[str, Any]]:
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
            logger.warning("Privacy review agent HTTP error: %s", last_error)
        parsed = parse_model_json(text, PrivacyReviewAgentResult)
        if parsed is not None:
            return parsed, {
                "attempts": attempts,
                "truncated_input": truncated,
                "model": settings.vllm_model,
            }
        last_error = "Model output was not valid PrivacyReviewAgentResult JSON"
        if attempt + 1 < retries:
            messages.append({"role": "assistant", "content": text or "(empty)"})
            messages.append(_repair_message(text or ""))

    return None, {
        "attempts": attempts,
        "truncated_input": truncated,
        "model": settings.vllm_model,
        "error": last_error,
    }
