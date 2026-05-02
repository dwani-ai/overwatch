from __future__ import annotations

import asyncio
import logging
import random
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from overwatch.config import Settings
from overwatch.models import JobStatus
from overwatch.store import JobStore

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).isoformat() if dt else None


@dataclass
class LiveIssState:
    enabled: bool
    running: bool = False
    throttled: bool = False
    throttle_reason: str | None = None
    throttle_delay_sec: float | None = None
    pending_jobs: int = 0
    processing_jobs: int = 0
    last_capture_at: datetime | None = None
    last_resolved_capture_url_at: datetime | None = None
    last_error: str | None = None
    last_segment_path: str | None = None
    failure_count: int = 0

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "running": self.running,
            "throttled": self.throttled,
            "throttle_reason": self.throttle_reason,
            "throttle_delay_sec": self.throttle_delay_sec,
            "pending_jobs": self.pending_jobs,
            "processing_jobs": self.processing_jobs,
            "last_capture_at": _iso(self.last_capture_at),
            "last_resolved_capture_url_at": _iso(self.last_resolved_capture_url_at),
            "last_error": self.last_error,
            "last_segment_path": self.last_segment_path,
            "failure_count": self.failure_count,
        }


class LiveIssCaptureLoop:
    """Capture fixed-duration MP4 windows from a live ISS stream URL."""

    def __init__(self, settings: Settings, store: JobStore, state: LiveIssState) -> None:
        self.settings = settings
        self.store = store
        self.state = state
        self._target_dir = (settings.ingest_dir / "live" / "iss").resolve()
        self._target_dir.mkdir(parents=True, exist_ok=True)
        self._resolved_capture_url: str | None = None
        self._resolved_url_expire_unix: int | None = None
        self._last_resolve_at: datetime | None = None

    async def run(self, stop: asyncio.Event) -> None:
        if not self.settings.live_iss_enabled:
            return
        self.state.running = True
        logger.info(
            "Live ISS capture enabled: interval=%.1fs segment=%.1fs target=%s",
            self.settings.live_iss_capture_interval_sec,
            self.settings.live_iss_segment_sec,
            self._target_dir,
        )
        try:
            while not stop.is_set():
                try:
                    pending, processing = await self._count_live_active_jobs()
                    self.state.pending_jobs = pending
                    self.state.processing_jobs = processing
                    if pending + processing >= self.settings.live_iss_max_pending_jobs:
                        overflow = max(
                            0,
                            (pending + processing) - self.settings.live_iss_max_pending_jobs + 1,
                        )
                        delay = self.settings.live_iss_capture_interval_sec * (1.0 + (0.5 * overflow))
                        self.state.throttled = True
                        self.state.throttle_reason = "backlog"
                        self.state.throttle_delay_sec = delay
                        await asyncio.wait_for(
                            stop.wait(),
                            timeout=delay,
                        )
                        continue

                    self.state.throttled = False
                    self.state.throttle_reason = None
                    self.state.throttle_delay_sec = None
                    capture_input = await self._capture_input_url()
                    await self._capture_one_segment(capture_input)
                    self.state.last_error = None
                    self.state.failure_count = 0
                    self.state.last_capture_at = _utc_now()
                    await self._enforce_retention()
                    await asyncio.wait_for(
                        stop.wait(),
                        timeout=self.settings.live_iss_capture_interval_sec,
                    )
                except TimeoutError:
                    continue
                except Exception as e:
                    if self.settings.live_iss_auto_resolve_capture_url and not self.settings.live_iss_capture_url.strip():
                        self._maybe_invalidate_resolved_url(str(e))
                    self.state.failure_count += 1
                    self.state.last_error = str(e)
                    delay = min(
                        120.0,
                        (2 ** min(6, self.state.failure_count)) + random.uniform(0.0, 2.0),
                    )
                    logger.warning(
                        "Live ISS capture failed (attempt=%d): %s; retry in %.1fs",
                        self.state.failure_count,
                        e,
                        delay,
                    )
                    self.state.throttle_reason = "capture_error_backoff"
                    self.state.throttle_delay_sec = delay
                    try:
                        await asyncio.wait_for(stop.wait(), timeout=delay)
                    except TimeoutError:
                        pass
        finally:
            self.state.running = False

    def _maybe_invalidate_resolved_url(self, err: str) -> None:
        low = err.lower()
        markers = ("403", "404", "forbidden", "signature", "expired", "manifest")
        if any(m in low for m in markers):
            self._resolved_capture_url = None
            self._resolved_url_expire_unix = None

    async def _count_live_active_jobs(self) -> tuple[int, int]:
        rows = await self.store.list_jobs(limit=500)
        pending_count = 0
        processing_count = 0
        for r in rows:
            if "/live/iss/" not in r.source_path:
                continue
            if r.status == JobStatus.pending:
                pending_count += 1
            elif r.status == JobStatus.processing:
                processing_count += 1
        return pending_count, processing_count

    async def _capture_input_url(self) -> str:
        static = self.settings.live_iss_capture_url.strip()
        if static:
            return static
        if not self.settings.live_iss_auto_resolve_capture_url:
            raise RuntimeError("LIVE_ISS_CAPTURE_URL is not configured")

        now = _utc_now()
        if self._resolved_capture_url and self._is_resolved_url_fresh(now):
            return self._resolved_capture_url

        url = await self._resolve_from_youtube()
        self._resolved_capture_url = url
        self._last_resolve_at = now
        self.state.last_resolved_capture_url_at = now
        self._resolved_url_expire_unix = self._parse_expire_unix(url)
        return url

    def _is_resolved_url_fresh(self, now: datetime) -> bool:
        if self._last_resolve_at is None:
            return False
        age_sec = (now - self._last_resolve_at).total_seconds()
        if age_sec >= self.settings.live_iss_resolve_interval_sec:
            return False
        if self._resolved_url_expire_unix is None:
            return True
        # Refresh a little before expiry so capture doesn't fail on edge.
        return int(now.timestamp()) + 120 < self._resolved_url_expire_unix

    def _parse_expire_unix(self, url: str) -> int | None:
        try:
            q = parse_qs(urlparse(url).query)
            raw = q.get("expire", [None])[0]
            if raw is None:
                return None
            return int(raw)
        except Exception:
            return None

    async def _resolve_from_youtube(self) -> str:
        watch = self.settings.live_iss_youtube_watch_url.strip()
        if not watch:
            raise RuntimeError(
                "No capture URL configured: set LIVE_ISS_CAPTURE_URL or LIVE_ISS_YOUTUBE_WATCH_URL"
            )
        attempts: list[list[str]] = [
            [
                self.settings.live_iss_yt_dlp_bin,
                "--js-runtimes",
                "node",
                "--remote-components",
                "ejs:github",
                "-g",
                watch,
            ],
            [
                self.settings.live_iss_yt_dlp_bin,
                "--remote-components",
                "ejs:github",
                "-g",
                watch,
            ],
            [self.settings.live_iss_yt_dlp_bin, "-g", watch],
        ]
        errors: list[str] = []
        for cmd in attempts:
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            except FileNotFoundError:
                raise RuntimeError(
                    f"{self.settings.live_iss_yt_dlp_bin} not found; install yt-dlp or set LIVE_ISS_CAPTURE_URL"
                ) from None
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                err = (stderr or b"").decode(errors="replace").strip() or "yt-dlp failed"
                errors.append(err)
                continue
            lines = [
                ln.strip() for ln in (stdout or b"").decode(errors="replace").splitlines() if ln.strip()
            ]
            if lines:
                return lines[0]
            errors.append("yt-dlp returned no URL output")
        raise RuntimeError(
            "Failed to resolve YouTube live URL: " + " | ".join(errors[-2:])
        )

    async def _capture_one_segment(self, source: str) -> None:

        now = _utc_now().strftime("%Y%m%dT%H%M%SZ")
        base = f"iss_{now}_{uuid.uuid4().hex[:8]}"
        out = (self._target_dir / f"{base}.mp4").resolve()
        # Keep .mp4 suffix on temp file so ffmpeg can infer muxer format.
        tmp = (self._target_dir / f".{base}.partial.mp4").resolve()

        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            source,
            "-t",
            f"{self.settings.live_iss_segment_sec:.2f}",
            "-vf",
            "scale=640:-2",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "30",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "96k",
            "-movflags",
            "+faststart",
            str(tmp),
        ]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        timeout = max(30.0, self.settings.live_iss_segment_sec + 30.0)
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError:
            proc.kill()
            await proc.communicate()
            tmp.unlink(missing_ok=True)
            raise RuntimeError("ffmpeg capture timed out") from None

        if proc.returncode != 0:
            err = (stderr or b"").decode(errors="replace").strip() or "unknown ffmpeg error"
            tmp.unlink(missing_ok=True)
            raise RuntimeError(f"ffmpeg capture failed ({proc.returncode}): {err}")

        if not tmp.exists() or tmp.stat().st_size <= 0:
            tmp.unlink(missing_ok=True)
            raise RuntimeError("captured segment file is empty")

        tmp.rename(out)
        self.state.last_segment_path = str(out)
        logger.info("Live ISS segment captured: %s", out)

    async def _enforce_retention(self) -> None:
        keep = max(1, self.settings.live_iss_retention_segments)
        files = [p for p in self._target_dir.glob("*.mp4") if p.is_file()]
        if len(files) <= keep:
            return
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        for old in files[keep:]:
            try:
                old.unlink(missing_ok=True)
            except Exception:
                logger.warning("Failed to delete old ISS segment: %s", old, exc_info=True)
