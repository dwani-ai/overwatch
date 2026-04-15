from __future__ import annotations

import logging
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


def extract_frames_for_indexing(
    video_path: Path,
    fps: float = 1.0,
    max_frames: int = 500,
    scale: int = 224,
) -> list[tuple[int, bytes]]:
    """
    Extract keyframes from a video file using ffmpeg (synchronous, thread-safe).

    Frames are scaled and padded to ``scale × scale`` (black bars), suitable
    for SigLIP-ViT input.  Only JPEG bytes are returned — no pixels are kept
    in memory beyond this call.

    Returns a list of ``(pts_ms, jpeg_bytes)`` tuples ordered by presentation
    timestamp.  ``pts_ms`` is approximate (derived from the sequential frame
    number and the requested ``fps``).
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        out_pattern = str(Path(tmpdir) / "frame_%06d.jpg")
        vf = (
            f"fps={fps},"
            f"scale={scale}:{scale}:force_original_aspect_ratio=decrease,"
            f"pad={scale}:{scale}:(ow-iw)/2:(oh-ih)/2:color=black"
        )
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "error",
            "-i", str(video_path),
            "-vf", vf,
            "-q:v", "4",
            out_pattern,
        ]
        proc = subprocess.run(cmd, capture_output=True)
        if proc.returncode != 0:
            err = proc.stderr.decode(errors="replace").strip()[:500]
            raise RuntimeError(f"ffmpeg frame extraction failed: {err}")

        frame_paths = sorted(Path(tmpdir).glob("frame_*.jpg"))
        total = len(frame_paths)
        if total == 0:
            return []

        # Subsample evenly if over budget
        if total > max_frames:
            step = total / max_frames
            frame_paths = [frame_paths[int(i * step)] for i in range(max_frames)]

        result: list[tuple[int, bytes]] = []
        for fp in frame_paths:
            # ffmpeg names frames starting at 1 (frame_000001.jpg → index 0)
            frame_num = int(fp.stem.split("_")[1])
            pts_ms = int((frame_num - 1) * 1000.0 / fps)
            result.append((pts_ms, fp.read_bytes()))

        return result
