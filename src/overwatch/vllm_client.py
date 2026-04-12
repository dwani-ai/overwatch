from __future__ import annotations

import base64
import logging
import time
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

    def to_event_payload(
        self,
        *,
        response_key: str = "data",
        **extra: Any,
    ) -> dict[str, Any]:
        """Plain dict for SQLite/JSON (never embed ``VllmCallResult`` itself)."""
        out: dict[str, Any] = {"ok": self.ok, **extra}
        if self.url is not None:
            out["url"] = self.url
        if self.status_code is not None:
            out["status_code"] = self.status_code
        if self.error is not None:
            out["error"] = self.error
        if self.body_preview is not None:
            out["body_preview"] = self.body_preview
        if self.data is not None:
            out[response_key] = self.data
        return out


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


def chunk_video_user_messages(*, instruction: str, mp4_bytes: bytes) -> list[dict[str, Any]]:
    """
    Build a single user message for OpenAI-style multimodal chat:
    text + ``video_url`` with MP4 data URI (vLLM / Gemma video inputs).
    """
    b64 = base64.standard_b64encode(mp4_bytes).decode("ascii")
    uri = f"data:video/mp4;base64,{b64}"
    content: list[dict[str, Any]] = [
        {"type": "text", "text": instruction},
        {"type": "video_url", "video_url": {"url": uri}},
    ]
    return [{"role": "user", "content": content}]


def _http_timeout(timeout_sec: float) -> httpx.Timeout:
    """Allow long reads/writes for large JSON bodies (base64 video)."""
    tw = max(timeout_sec, 120.0)
    return httpx.Timeout(connect=60.0, read=timeout_sec, write=tw, pool=60.0)


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
    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(
            timeout=_http_timeout(timeout_sec),
            follow_redirects=True,
        ) as client:
            r = await client.post(url, headers=_headers(api_key), json=body)
            if r.status_code >= 400:
                res = VllmCallResult(
                    ok=False,
                    url=url,
                    status_code=r.status_code,
                    error=f"HTTP {r.status_code}",
                    body_preview=_truncate(r.text),
                )
                logger.info(
                    "vllm_chat_completion ok=%s http=%s duration_ms=%.0f model=%s",
                    False,
                    r.status_code,
                    (time.perf_counter() - t0) * 1000,
                    model,
                )
                return res
            res = VllmCallResult(ok=True, data=r.json(), url=url, status_code=r.status_code)
            logger.info(
                "vllm_chat_completion ok=%s http=%s duration_ms=%.0f model=%s",
                True,
                r.status_code,
                (time.perf_counter() - t0) * 1000,
                model,
            )
            return res
    except httpx.HTTPStatusError as e:
        txt = e.response.text if e.response is not None else ""
        sc = e.response.status_code if e.response else None
        logger.info(
            "vllm_chat_completion ok=%s http=%s duration_ms=%.0f model=%s",
            False,
            sc,
            (time.perf_counter() - t0) * 1000,
            model,
        )
        return VllmCallResult(
            ok=False,
            url=url,
            status_code=sc,
            error=str(e),
            body_preview=_truncate(txt),
        )
    except Exception as e:
        logger.warning(
            "vLLM chat/completions failed after %.0fms: %s",
            (time.perf_counter() - t0) * 1000,
            e,
        )
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
