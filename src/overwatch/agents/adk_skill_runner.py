"""ADK ``LlmAgent`` + ``SkillToolset`` execution for Overwatch job-level agents (vLLM via LiteLLM)."""

from __future__ import annotations

import json
import logging
import os
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from pydantic import BaseModel

from overwatch.analysis.json_extract import parse_model_json
from overwatch.config import Settings
from overwatch.models import (
    AgentKind,
    ComplianceBriefAgentResult,
    IncidentBriefAgentResult,
    LossPreventionAgentResult,
    PerimeterChainAgentResult,
    PrivacyReviewAgentResult,
    RiskReviewAgentResult,
    SynthesisAgentResult,
)

logger = logging.getLogger(__name__)

_MAX_SUMMARY_JSON_CHARS = 200_000

_SKILL_ROOT = Path(__file__).resolve().parent / "skills"

_ROOT_INSTRUCTION = """You are an Overwatch job agent. The user message contains the job summary JSON and the exact JSON schema you must output.

Mandatory workflow:
1. Call the list_skills tool to see the available skill.
2. Call load_skill with that skill name to load full instructions (L2).
3. Apply those instructions to the job summary. Use load_skill_resource only if the skill tells you to load a reference file.
4. Your final assistant message must be ONLY one JSON object matching the schema in the user message. No markdown fences, no commentary.

Global rules: Ground answers only in the provided JSON. Do not invent facts. Do not output personal identities."""

_AGENT_TEMPERATURE: dict[AgentKind, float] = {
    AgentKind.synthesis: 0.2,
    AgentKind.risk_review: 0.15,
    AgentKind.incident_brief: 0.2,
    AgentKind.compliance_brief: 0.15,
    AgentKind.loss_prevention: 0.2,
    AgentKind.perimeter_chain: 0.2,
    AgentKind.privacy_review: 0.15,
}

_RESULT_MODEL: dict[AgentKind, type[BaseModel]] = {
    AgentKind.synthesis: SynthesisAgentResult,
    AgentKind.risk_review: RiskReviewAgentResult,
    AgentKind.incident_brief: IncidentBriefAgentResult,
    AgentKind.compliance_brief: ComplianceBriefAgentResult,
    AgentKind.loss_prevention: LossPreventionAgentResult,
    AgentKind.perimeter_chain: PerimeterChainAgentResult,
    AgentKind.privacy_review: PrivacyReviewAgentResult,
}

_PARSE_ERROR_LABEL: dict[AgentKind, str] = {
    AgentKind.synthesis: "Model output was not valid SynthesisAgentResult JSON",
    AgentKind.risk_review: "Model output was not valid RiskReviewAgentResult JSON",
    AgentKind.incident_brief: "Model output was not valid IncidentBriefAgentResult JSON",
    AgentKind.compliance_brief: "Model output was not valid ComplianceBriefAgentResult JSON",
    AgentKind.loss_prevention: "Model output was not valid LossPreventionAgentResult JSON",
    AgentKind.perimeter_chain: "Model output was not valid PerimeterChainAgentResult JSON",
    AgentKind.privacy_review: "Model output was not valid PrivacyReviewAgentResult JSON",
}

_USER_BODY: dict[AgentKind, str] = {
    AgentKind.synthesis: """Input job summary (JSON). It may be truncated if very large; call that out mentally and stay conservative.

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
""",
    AgentKind.risk_review: """Job summary (JSON). It may be truncated if very large; stay conservative.

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
""",
    AgentKind.incident_brief: """Job summary (JSON). It may be truncated if very large; stay conservative.

{payload}

Required JSON shape (all keys required):
{{
  "schema_version": "1",
  "narrative": "<2-6 sentences>",
  "key_moments": ["<bullet>", "..."],
  "situational_factors": ["<bullet>", "..."],
  "suggested_followups": ["<action>", "..."]
}}
""",
    AgentKind.compliance_brief: """Job summary (JSON). May be truncated; stay conservative.

{payload}

Required JSON shape (all keys required):
{{
  "schema_version": "1",
  "overall_alignment": "aligned" | "partial" | "unclear" | "concerns",
  "observed_practices": ["<bullet>", "..."],
  "gaps_or_concerns": ["<bullet>", "..."],
  "recommended_verifications": ["<bullet>", "..."],
  "notes": "<short paragraph>"
}}
""",
    AgentKind.loss_prevention: """Job summary (JSON). May be truncated; stay conservative.

{payload}

Required JSON shape (all keys required):
{{
  "schema_version": "1",
  "narrative": "<2-6 sentences, LP style>",
  "behavioral_observations": ["<bullet>", "..."],
  "risk_level": "low" | "medium" | "high" | "unknown",
  "suggested_actions": ["<bullet>", "..."]
}}
""",
    AgentKind.perimeter_chain: """Job summary (JSON). May be truncated; stay conservative.

{payload}

Required JSON shape (all keys required):
{{
  "schema_version": "1",
  "chain_narrative": "<ordered story, 2-7 sentences>",
  "key_events": ["<bullet>", "..."],
  "zones_or_segments": ["<area label from text>", "..."],
  "follow_up_checks": ["<bullet>", "..."]
}}
""",
    AgentKind.privacy_review: """Job summary (JSON). May be truncated; note truncation in your assessment if relevant.

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
""",
}

def _prepare_summary_blob(summary: dict[str, Any]) -> tuple[str, bool]:
    raw = json.dumps(summary, ensure_ascii=False, indent=2)
    if len(raw) <= _MAX_SUMMARY_JSON_CHARS:
        return raw, False
    return raw[:_MAX_SUMMARY_JSON_CHARS] + "\n…", True


def _repair_user_message(invalid_snippet: str) -> str:
    return (
        "That response was not valid JSON for the required schema. "
        "Reply with ONLY one JSON object, no markdown fences, no commentary.\n\n"
        f"Invalid output (truncated):\n{invalid_snippet[:1500]}"
    )


@contextmanager
def _litellm_openai_env(base_url: str, api_key: str | None) -> Iterator[None]:
    """Point LiteLLM ``openai/...`` at the vLLM OpenAI-compatible root."""
    base = base_url.strip().rstrip("/")
    prev_base = os.environ.get("OPENAI_API_BASE")
    prev_key = os.environ.get("OPENAI_API_KEY")
    try:
        os.environ["OPENAI_API_BASE"] = base
        if api_key is not None:
            os.environ["OPENAI_API_KEY"] = api_key
        elif "OPENAI_API_KEY" in os.environ:
            del os.environ["OPENAI_API_KEY"]
        yield
    finally:
        if prev_base is None:
            os.environ.pop("OPENAI_API_BASE", None)
        else:
            os.environ["OPENAI_API_BASE"] = prev_base
        if prev_key is None:
            os.environ.pop("OPENAI_API_KEY", None)
        else:
            os.environ["OPENAI_API_KEY"] = prev_key


# ADK requires SKILL.md ``name`` to be kebab-case and the *directory name* must match exactly.
_AGENT_SKILL_SUBDIR: dict[AgentKind, str] = {
    AgentKind.synthesis: "synthesis",
    AgentKind.risk_review: "risk-review",
    AgentKind.incident_brief: "incident-brief",
    AgentKind.compliance_brief: "compliance-brief",
    AgentKind.loss_prevention: "loss-prevention",
    AgentKind.perimeter_chain: "perimeter-chain",
    AgentKind.privacy_review: "privacy-review",
}


def _skill_dir(kind: AgentKind) -> Path:
    return _SKILL_ROOT / _AGENT_SKILL_SUBDIR[kind]


async def _run_llm_agent_once(
    *,
    settings: Settings,
    agent_kind: AgentKind,
    user_text: str,
    temperature: float,
) -> tuple[str | None, str | None]:
    """One ADK invocation; returns (assistant_text, transport_error)."""
    from google.adk.agents import LlmAgent
    from google.adk.models.lite_llm import LiteLlm
    from google.adk.runners import InMemoryRunner
    from google.adk.skills import load_skill_from_dir
    from google.adk.tools.skill_toolset import SkillToolset
    from google.genai import types

    skill_path = _skill_dir(agent_kind)
    if not skill_path.is_dir():
        return None, f"Skill directory not found: {skill_path}"

    skill = load_skill_from_dir(skill_path)
    skill_toolset = SkillToolset(skills=[skill])

    model_name = f"openai/{settings.vllm_model}"
    with _litellm_openai_env(settings.vllm_base_url.strip(), settings.vllm_api_key):
        lite = LiteLlm(
            model=model_name,
            api_base=settings.vllm_base_url.strip().rstrip("/"),
            api_key=settings.vllm_api_key or "",
            timeout=int(settings.vllm_agent_timeout_sec),
        )

        agent = LlmAgent(
            model=lite,
            name=f"overwatch_{agent_kind.value}",
            instruction=_ROOT_INSTRUCTION,
            tools=[skill_toolset],
            generate_content_config=types.GenerateContentConfig(
                temperature=temperature,
                max_output_tokens=settings.vllm_agent_max_tokens,
            ),
        )

        app_name = "overwatch_job_agents"
        runner = InMemoryRunner(agent=agent, app_name=app_name)
        session = await runner.session_service.create_session(
            app_name=app_name,
            user_id="overwatch",
            session_id=str(uuid.uuid4()),
        )
        msg = types.Content(role="user", parts=[types.Part(text=user_text)])

        last_text: str | None = None
        try:
            async for event in runner.run_async(
                user_id=session.user_id,
                session_id=session.id,
                new_message=msg,
            ):
                if event.is_final_response() and event.content and event.content.parts:
                    texts: list[str] = []
                    for part in event.content.parts:
                        if part.text:
                            texts.append(part.text)
                    if texts:
                        last_text = "".join(texts)
        except Exception as e:
            logger.warning("ADK runner error for %s: %s", agent_kind.value, e, exc_info=True)
            return None, str(e)

    return last_text, None


async def run_job_agent_adk(
    settings: Settings,
    agent_kind: AgentKind,
    summary: dict[str, Any],
) -> tuple[BaseModel | None, dict[str, Any]]:
    """
    Run one job-level agent via ADK ``SkillToolset`` + LiteLLM (vLLM).

    Returns ``(result | None, meta)`` where ``meta`` includes ``attempts``, ``truncated_input``, and optional ``error``.
    """
    base = settings.vllm_base_url.strip()
    if not base:
        return None, {"error": "VLLM_BASE_URL is not set", "attempts": 0, "truncated_input": False}

    out_model = _RESULT_MODEL[agent_kind]
    temp = _AGENT_TEMPERATURE[agent_kind]
    user_template = _USER_BODY[agent_kind]
    err_label = _PARSE_ERROR_LABEL[agent_kind]

    blob, truncated = _prepare_summary_blob(summary)
    retries = max(1, settings.vllm_json_retry_max)
    attempts = 0
    last_error: str | None = None

    user_text = user_template.format(payload=blob)
    last_model_text: str | None = None

    for attempt in range(retries):
        attempts = attempt + 1
        combined = (
            user_text
            if attempt == 0
            else user_text + "\n\n" + _repair_user_message(last_model_text or "")
        )
        text, transport_err = await _run_llm_agent_once(
            settings=settings,
            agent_kind=agent_kind,
            user_text=combined,
            temperature=temp,
        )
        last_model_text = text
        if transport_err:
            last_error = transport_err
            logger.warning("Job agent %s transport error: %s", agent_kind.value, transport_err)
        if text:
            parsed = parse_model_json(text, out_model)
            if parsed is not None:
                return parsed, {
                    "attempts": attempts,
                    "truncated_input": truncated,
                    "model": settings.vllm_model,
                }
            last_error = err_label
        else:
            last_error = last_error or "Empty model response"

    return None, {
        "attempts": attempts,
        "truncated_input": truncated,
        "model": settings.vllm_model,
        "error": last_error,
    }
