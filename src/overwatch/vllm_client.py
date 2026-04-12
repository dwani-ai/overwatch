from __future__ import annotations

import logging
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


async def fetch_models(
    openai_base: str,
    *,
    api_key: str | None = None,
    timeout_sec: float = 15.0,
) -> dict[str, Any] | None:
    url = models_url(openai_base)
    try:
        async with httpx.AsyncClient(timeout=timeout_sec) as client:
            r = await client.get(url, headers=_headers(api_key))
            r.raise_for_status()
            return r.json()
    except Exception as e:
        logger.warning("vLLM models GET failed: %s", e)
        return None


async def chat_completion(
    openai_base: str,
    *,
    model: str,
    messages: list[dict[str, Any]],
    api_key: str | None = None,
    timeout_sec: float = DEFAULT_CHAT_TIMEOUT_SEC,
    max_tokens: int | None = 512,
    temperature: float = 0.2,
) -> dict[str, Any] | None:
    url = chat_completions_url(openai_base)
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }
    if max_tokens is not None:
        body["max_tokens"] = max_tokens
    try:
        async with httpx.AsyncClient(timeout=timeout_sec) as client:
            r = await client.post(url, headers=_headers(api_key), json=body)
            r.raise_for_status()
            return r.json()
    except Exception as e:
        logger.warning("vLLM chat/completions failed: %s", e)
        return None


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
    return None
