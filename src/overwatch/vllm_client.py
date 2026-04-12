from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DEFAULT_CHAT_TIMEOUT_SEC = 120.0


def chat_completions_url(openai_base: str) -> str:
    """``openai_base`` is the prefix before ``/chat/completions`` (usually ends with ``/v1``)."""
    return openai_base.rstrip("/") + "/chat/completions"


def models_url(openai_base: str) -> str:
    return openai_base.rstrip("/") + "/models"


def _headers(api_key: str | None) -> dict[str, str]:
    h = {"Content-Type": "application/json"}
    if api_key:
        h["Authorization"] = f"Bearer {api_key}"
    return h


def _truncate(s: str, max_len: int = 800) -> str:
    s = s.strip()
    if len(s) <= max_len:
        return s
    return s[:max_len] + "…"


@dataclass(frozen=True)
class VllmCallResult:
    ok: bool
    data: dict[str, Any] | None = None
    url: str | None = None
    status_code: int | None = None
    error: str | None = None
    body_preview: str | None = None


async def fetch_models(
    openai_base: str,
    *,
    api_key: str | None = None,
    timeout_sec: float = 15.0,
) -> VllmCallResult:
    url = models_url(openai_base)
    try:
        async with httpx.AsyncClient(timeout=timeout_sec, follow_redirects=True) as client:
            r = await client.get(url, headers=_headers(api_key))
            if r.status_code >= 400:
                return VllmCallResult(
                    ok=False,
                    url=url,
                    status_code=r.status_code,
                    error=f"HTTP {r.status_code}",
                    body_preview=_truncate(r.text),
                )
            return VllmCallResult(ok=True, data=r.json(), url=url, status_code=r.status_code)
    except httpx.HTTPStatusError as e:
        txt = e.response.text if e.response is not None else ""
        return VllmCallResult(
            ok=False,
            url=url,
            status_code=e.response.status_code if e.response else None,
            error=str(e),
            body_preview=_truncate(txt),
        )
    except Exception as e:
        logger.warning("vLLM models GET failed: %s", e)
        return VllmCallResult(ok=False, url=url, error=type(e).__name__ + ": " + str(e))


async def chat_completion(
    openai_base: str,
    *,
    model: str,
    messages: list[dict[str, Any]],
    api_key: str | None = None,
    timeout_sec: float = DEFAULT_CHAT_TIMEOUT_SEC,
    max_tokens: int | None = 512,
    temperature: float = 0.2,
) -> VllmCallResult:
    url = chat_completions_url(openai_base)
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }
    if max_tokens is not None:
        body["max_tokens"] = max_tokens
    try:
        async with httpx.AsyncClient(timeout=timeout_sec, follow_redirects=True) as client:
            r = await client.post(url, headers=_headers(api_key), json=body)
            if r.status_code >= 400:
                return VllmCallResult(
                    ok=False,
                    url=url,
                    status_code=r.status_code,
                    error=f"HTTP {r.status_code}",
                    body_preview=_truncate(r.text),
                )
            return VllmCallResult(ok=True, data=r.json(), url=url, status_code=r.status_code)
    except httpx.HTTPStatusError as e:
        txt = e.response.text if e.response is not None else ""
        return VllmCallResult(
            ok=False,
            url=url,
            status_code=e.response.status_code if e.response else None,
            error=str(e),
            body_preview=_truncate(txt),
        )
    except Exception as e:
        logger.warning("vLLM chat/completions failed: %s", e)
        return VllmCallResult(ok=False, url=url, error=type(e).__name__ + ": " + str(e))


def extract_assistant_text(response: dict[str, Any] | None) -> str | None:
    if not response:
        return None
    choices = response.get("choices")
    if not choices:
        return None
    msg = choices[0].get("message") or {}
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                t = block.get("text")
                if isinstance(t, str):
                    parts.append(t)
        if parts:
            return "".join(parts)
    return None
