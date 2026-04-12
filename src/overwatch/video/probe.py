from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class VideoProbe:
    duration_sec: float | None
    avg_frame_rate: float | None
    width: int | None
    height: int | None
    codec: str | None


def _parse_frame_rate(rate: str | None) -> float | None:
    if not rate or rate in ("0/0", "nan"):
        return None
    if "/" in rate:
        num, _, den = rate.partition("/")
        try:
            n, d = float(num), float(den)
            if d == 0:
                return None
            return n / d
        except ValueError:
            return None
    try:
        return float(rate)
    except ValueError:
        return None


async def ffprobe(path: Path) -> VideoProbe:
    """Run ffprobe and return stream metadata (first video stream)."""
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=codec_name,width,height,avg_frame_rate,duration",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(path),
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        logger.error("ffprobe not found; install ffmpeg")
        raise

    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        err = stderr.decode(errors="replace").strip()
        raise RuntimeError(f"ffprobe failed ({proc.returncode}): {err}")

    data = json.loads(stdout.decode())
    streams = data.get("streams") or []
    fmt = data.get("format") or {}

    duration = None
    if fmt.get("duration") is not None:
        try:
            duration = float(fmt["duration"])
        except (TypeError, ValueError):
            pass

    if not streams:
        return VideoProbe(
            duration_sec=duration,
            avg_frame_rate=None,
            width=None,
            height=None,
            codec=None,
        )

    s0 = streams[0]
    stream_dur = s0.get("duration")
    if stream_dur is not None:
        try:
            duration = float(stream_dur)
        except (TypeError, ValueError):
            pass

    w, h = s0.get("width"), s0.get("height")
    return VideoProbe(
        duration_sec=duration,
        avg_frame_rate=_parse_frame_rate(s0.get("avg_frame_rate")),
        width=int(w) if w is not None else None,
        height=int(h) if h is not None else None,
        codec=s0.get("codec_name"),
    )
