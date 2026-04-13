from __future__ import annotations

import logging
import struct
from typing import Any

logger = logging.getLogger(__name__)

_PNG_SIG = b"\x89PNG\r\n\x1a\n"


def png_screen_dimensions(png: bytes) -> tuple[int, int] | None:
    """
    Read width/height from a PNG without Pillow (IHDR chunk).
    Returns ``None`` if bytes are not a valid PNG header.
    """
    if len(png) < 24 or not png.startswith(_PNG_SIG):
        return None
    if png[12:16] != b"IHDR":
        return None
    w, h = struct.unpack(">II", png[16:24])
    if w <= 0 or h <= 0:
        return None
    return w, h


class CaptureError(RuntimeError):
    """Screen capture failed (no display, permission, or missing dependency)."""


def capture_screen_png_with_offset(*, monitor: int = 1) -> tuple[bytes, int, int]:
    """
    Grab the given monitor (1 = primary in mss).

    Returns ``(png_bytes, origin_left, origin_top)`` in global screen coordinates so
    PNG-relative ``(x, y)`` clicks map to ``origin_left + x``, ``origin_top + y`` for pyautogui.
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
            left = int(region["left"])
            top = int(region["top"])
            shot = sct.grab(region)
            png = mss.tools.to_png(shot.rgb, shot.size)  # type: ignore[no-any-return]
            return png, left, top
    except CaptureError:
        raise
    except Exception as e:
        logger.warning("screen capture failed: %s", e)
        raise CaptureError(str(e)) from e


def capture_screen_png(*, monitor: int = 1) -> bytes:
    """
    Grab the given monitor (1 = primary in mss) and return PNG bytes.

    Requires ``mss`` and a graphical session. Raises :class:`CaptureError` on failure.
    """
    png, _, _ = capture_screen_png_with_offset(monitor=monitor)
    return png


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
