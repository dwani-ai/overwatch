from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class CaptureError(RuntimeError):
    """Screen capture failed (no display, permission, or missing dependency)."""


def capture_screen_png(*, monitor: int = 1) -> bytes:
    """
    Grab the given monitor (1 = primary in mss) and return PNG bytes.

    Requires ``mss`` and a graphical session. Raises :class:`CaptureError` on failure.
    """
    try:
        import mss
        import mss.tools
    except ImportError as e:
        raise CaptureError("mss is required for screen capture; install requirements.txt") from e

    try:
        with mss.mss() as sct:
            if monitor < 0 or monitor >= len(sct.monitors):
                raise CaptureError(f"invalid monitor index {monitor}")
            region = sct.monitors[monitor]
            shot = sct.grab(region)
            return mss.tools.to_png(shot.rgb, shot.size)  # type: ignore[no-any-return]
    except CaptureError:
        raise
    except Exception as e:
        logger.warning("screen capture failed: %s", e)
        raise CaptureError(str(e)) from e


def capture_region_png(*, left: int, top: int, width: int, height: int) -> bytes:
    """Capture a rectangle in screen coordinates (pixels)."""
    try:
        import mss
        import mss.tools
    except ImportError as e:
        raise CaptureError("mss is required for screen capture; install requirements.txt") from e

    region: dict[str, Any] = {"left": left, "top": top, "width": width, "height": height}
    try:
        with mss.mss() as sct:
            shot = sct.grab(region)
            return mss.tools.to_png(shot.rgb, shot.size)  # type: ignore[no-any-return]
    except Exception as e:
        logger.warning("region capture failed: %s", e)
        raise CaptureError(str(e)) from e
