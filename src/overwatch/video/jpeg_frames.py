from __future__ import annotations

import asyncio
import logging
from typing import Final

logger = logging.getLogger(__name__)

_JPEG_START: Final[bytes] = b"\xff\xd8"
_JPEG_END: Final[bytes] = b"\xff\xd9"


def split_mjpeg_stream(buf: bytes) -> list[bytes]:
    """Split concatenated MJPEG bytes (ffmpeg ``image2pipe``) into individual JPEG blobs."""
    frames: list[bytes] = []
    i = 0
    n = len(buf)
    while i < n:
        start = buf.find(_JPEG_START, i)
        if start < 0:
            break
        end = buf.find(_JPEG_END, start + 2)
        if end < 0:
            break
        frames.append(buf[start : end + 2])
        i = end + 2
    return frames


async def extract_mjpeg_frames_from_mp4_bytes(
    mp4_bytes: bytes,
    *,
    max_frames: int = 8,
    max_width: int = 768,
    sample_fps: float = 0.5,
) -> list[bytes]:
    """
    Sample stills from an in-memory MP4 segment via ffmpeg (MJPEG over pipe).

    Used for OpenAI backends that support ``image_url`` but not ``video_url`` (e.g. llama.cpp server).
    """
    if max_frames < 1 or sample_fps <= 0:
        raise ValueError("invalid max_frames or sample_fps")
    vf = f"fps={sample_fps},scale={max_width}:-2"
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        "pipe:0",
        "-vf",
        vf,
        "-frames:v",
        str(max_frames),
        "-f",
        "image2pipe",
        "-vcodec",
        "mjpeg",
        "-q:v",
        "4",
        "pipe:1",
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        logger.error("ffmpeg not found")
        raise

    stdout, stderr = await proc.communicate(mp4_bytes)
    if proc.returncode != 0:
        err = (stderr or b"").decode(errors="replace").strip()
        raise RuntimeError(f"ffmpeg frame extract failed ({proc.returncode}): {err}")
    frames = split_mjpeg_stream(stdout or b"")
    if not frames:
        logger.warning("ffmpeg produced no decodable JPEG frames from segment")
    return frames
