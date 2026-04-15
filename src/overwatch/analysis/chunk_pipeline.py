from __future__ import annotations

import logging
from typing import Any

from overwatch.analysis.json_extract import parse_model_json
from overwatch.config import Settings
from overwatch.models import (
    AttendanceOut,
    ChunkAnalysisMerged,
    ChunkPlanItem,
    ObservationsPass,
    SpecialistMainOut,
    SpecialistSecLogOut,
)
from overwatch.vllm_client import (
    chat_completion,
    chunk_video_user_messages,
    extract_assistant_text,
)

logger = logging.getLogger(__name__)

_OBSERVE_INSTRUCTION = """You are a warehouse/CCTV vision analyst. Watch the attached MP4 clip.

The clip corresponds to approximately {start_ms} ms → {end_ms} ms in the parent recording (~{duration_sec:.1f} s).

Output ONLY a single JSON object (no markdown, no code fences, no text before or after) with exactly this shape:
{{"scene_summary": "<one paragraph of what is visible overall>", "observations": [{{"what": "<fact>", "where_approx": "<optional>", "when_hint": "<optional>"}}]}}

Rules:
- List concrete visible facts: people, equipment, pallets, vehicles, motion, doors, interactions.
- **No personal identities** (no names, no face recognition claims).
- If unsure, still list best-effort observations with cautious wording in "what".
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


async def _complete_json_multimodal(
    *,
    openai_base: str,
    model: str,
    api_key: str | None,
    timeout_sec: float,
    max_tokens: int,
    messages: list[dict[str, Any]],
    out_model: type[ObservationsPass],
    retries: int,
) -> tuple[ObservationsPass | None, int]:
    msgs = list(messages)
    attempts = 0
    for attempt in range(max(1, retries)):
        attempts = attempt + 1
        res = await chat_completion(
            openai_base,
            model=model,
            messages=msgs,
            api_key=api_key,
            timeout_sec=timeout_sec,
            max_tokens=max_tokens,
            temperature=0.1,
        )
        text = extract_assistant_text(res.data)
        if not res.ok:
            logger.warning("Multimodal LLM call failed: %s", res.error)
        parsed = parse_model_json(text, out_model)
        if parsed is not None:
            return parsed, attempts
        if attempt + 1 < retries:
            msgs.append({"role": "assistant", "content": text or "(empty)"})
            msgs.append(_repair_message(text or ""))
    return None, attempts


async def _complete_json_text(
    *,
    openai_base: str,
    model: str,
    api_key: str | None,
    timeout_sec: float,
    max_tokens: int,
    system_and_user: list[dict[str, Any]],
    out_model: type[Any],
    retries: int,
) -> tuple[Any | None, int]:
    msgs = list(system_and_user)
    attempts = 0
    for attempt in range(max(1, retries)):
        attempts = attempt + 1
        res = await chat_completion(
            openai_base,
            model=model,
            messages=msgs,
            api_key=api_key,
            timeout_sec=timeout_sec,
            max_tokens=max_tokens,
            temperature=0.1,
        )
        text = extract_assistant_text(res.data)
        if not res.ok:
            logger.warning("Specialist LLM call failed: %s", res.error)
        parsed = parse_model_json(text, out_model)
        if parsed is not None:
            return parsed, attempts
        if attempt + 1 < retries:
            msgs.append({"role": "assistant", "content": text or "(empty)"})
            msgs.append(_repair_message(text or ""))
    return None, attempts


async def run_structured_chunk_analysis(
    *,
    openai_base: str,
    vllm_model: str,
    api_key: str | None,
    chunk: ChunkPlanItem,
    mp4_bytes: bytes,
    settings: Settings,
) -> dict[str, Any]:
    """
    One multimodal **observe** pass (strict JSON) + three **text** specialist passes.
    Returns a single serializable payload for a ``chunk_analysis`` event + job summary merging.
    """
    duration_sec = max(0.01, (chunk.end_pts_ms - chunk.start_pts_ms) / 1000.0)
    observe_instr = _OBSERVE_INSTRUCTION.format(
        start_ms=chunk.start_pts_ms,
        end_ms=chunk.end_pts_ms,
        duration_sec=duration_sec,
    )
    mm = chunk_video_user_messages(instruction=observe_instr, mp4_bytes=mp4_bytes)

    obs, obs_attempts = await _complete_json_multimodal(
        openai_base=openai_base,
        model=vllm_model,
        api_key=api_key,
        timeout_sec=settings.vllm_chunk_timeout_sec,
        max_tokens=settings.vllm_chunk_max_tokens,
        messages=mm,
        out_model=ObservationsPass,
        retries=settings.vllm_json_retry_max,
    )

    if obs is None:
        merged = ChunkAnalysisMerged(
            chunk_index=chunk.chunk_index,
            start_pts_ms=chunk.start_pts_ms,
            end_pts_ms=chunk.end_pts_ms,
            start_frame=chunk.start_frame,
            end_frame=chunk.end_frame,
            scene_summary="",
            main_events=[],
            security=[],
            logistics=[],
            attendance=AttendanceOut(),
        )
        return {
            "chunk_index": chunk.chunk_index,
            "start_pts_ms": chunk.start_pts_ms,
            "end_pts_ms": chunk.end_pts_ms,
            "start_frame": chunk.start_frame,
            "end_frame": chunk.end_frame,
            "segment_bytes": len(mp4_bytes),
            "merged": merged.model_dump(),
            "meta": {
                "observe_ok": False,
                "specialist_ok": {
                    "main_events": False,
                    "security_logistics": False,
                    "attendance": False,
                },
                "attempts": {
                    "observe": obs_attempts,
                    "main_events": 0,
                    "security_logistics": 0,
                    "attendance": 0,
                },
            },
        }

    obs_json = obs.model_dump_json()
    ctx = (
        "You are an Overwatch analytics specialist. Use ONLY the JSON input below; "
        "do not invent facts not supported by it. Output ONLY valid JSON, no markdown.\n\nINPUT:\n"
        f"{obs_json}"
    )

    main, main_attempts = await _complete_json_text(
        openai_base=openai_base,
        model=vllm_model,
        api_key=api_key,
        timeout_sec=settings.vllm_chat_timeout_sec,
        max_tokens=settings.vllm_specialist_max_tokens,
        system_and_user=[
            {
                "role": "user",
                "content": ctx
                + '\n\nTASK: Emit {"main_events":[{"title":"","detail":"","confidence":null}]} '
                "with concise items derived from the input.",
            },
        ],
        out_model=SpecialistMainOut,
        retries=settings.vllm_json_retry_max,
    )

    sec_log, sec_attempts = await _complete_json_text(
        openai_base=openai_base,
        model=vllm_model,
        api_key=api_key,
        timeout_sec=settings.vllm_chat_timeout_sec,
        max_tokens=settings.vllm_specialist_max_tokens,
        system_and_user=[
            {
                "role": "user",
                "content": ctx
                + '\n\nTASK: Emit {"security":[{"category":"","description":"","severity":"unknown",'
                '"confidence":null}],"logistics":[{"label":"","description":"","action":"unknown"}]}. '
                "Use severity one of low|medium|high|info|unknown.",
            },
        ],
        out_model=SpecialistSecLogOut,
        retries=settings.vllm_json_retry_max,
    )

    att, att_attempts = await _complete_json_text(
        openai_base=openai_base,
        model=vllm_model,
        api_key=api_key,
        timeout_sec=settings.vllm_chat_timeout_sec,
        max_tokens=settings.vllm_specialist_max_tokens,
        system_and_user=[
            {
                "role": "user",
                "content": ctx
                + '\n\nTASK: Emit {"approx_people_visible":null,"entries":0,"exits":0,"notes":null} '
                "— **counts only**, no identities.",
            },
        ],
        out_model=AttendanceOut,
        retries=settings.vllm_json_retry_max,
    )

    merged = ChunkAnalysisMerged(
        chunk_index=chunk.chunk_index,
        start_pts_ms=chunk.start_pts_ms,
        end_pts_ms=chunk.end_pts_ms,
        start_frame=chunk.start_frame,
        end_frame=chunk.end_frame,
        scene_summary=obs.scene_summary if obs else "",
        main_events=list(main.main_events) if main else [],
        security=list(sec_log.security) if sec_log else [],
        logistics=list(sec_log.logistics) if sec_log else [],
        attendance=att if att else AttendanceOut(),
    )

    return {
        "chunk_index": chunk.chunk_index,
        "start_pts_ms": chunk.start_pts_ms,
        "end_pts_ms": chunk.end_pts_ms,
        "start_frame": chunk.start_frame,
        "end_frame": chunk.end_frame,
        "segment_bytes": len(mp4_bytes),
        "merged": merged.model_dump(),
        "observations": [o.model_dump() for o in obs.observations] if obs else [],
        "meta": {
            "observe_ok": obs is not None,
            "specialist_ok": {
                "main_events": main is not None,
                "security_logistics": sec_log is not None,
                "attendance": att is not None,
            },
            "attempts": {
                "observe": obs_attempts,
                "main_events": main_attempts,
                "security_logistics": sec_attempts,
                "attendance": att_attempts,
            },
        },
    }


def job_summary_from_chunks(
    *,
    source_path: str,
    duration_sec: float | None,
    planned_chunks: int,
    analyses: list[dict[str, Any]],
) -> dict[str, Any]:
    merged_list: list[dict[str, Any]] = []
    for a in analyses:
        m = a.get("merged")
        if isinstance(m, dict):
            merged_list.append(m)
    payload = {
        "schema_version": "1",
        "source_path": source_path,
        "duration_sec": duration_sec,
        "planned_chunk_count": planned_chunks,
        "analysed_chunk_count": len(analyses),
        "chunk_analyses": merged_list,
    }
    return payload
