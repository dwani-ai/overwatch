from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from overwatch.factorio.capture import CaptureError, capture_screen_png
from overwatch.factorio.session import FactorioSessionStore

logger = logging.getLogger(__name__)


async def capture_loop(
    store: FactorioSessionStore,
    session_id: str,
    *,
    interval_sec: float,
    max_frames: int | None = None,
    monitor: int = 1,
    capture_fn: Callable[[], bytes] | None = None,
    on_frame: Callable[[int, bytes], Awaitable[None]] | None = None,
) -> AsyncIterator[int]:
    """
    Yield step indices as PNG frames are written to ``store``.

    ``capture_fn`` overrides default full-screen capture (for tests). ``on_frame`` is optional side effect.
    """
    step = 0
    fn = capture_fn or (lambda: capture_screen_png(monitor=monitor))
    while max_frames is None or step < max_frames:
        try:
            png = await asyncio.to_thread(fn)
        except CaptureError as e:
            logger.warning("capture_loop: %s", e)
            raise
        store.append_frame(session_id, step, png)
        if on_frame is not None:
            await on_frame(step, png)
        yield step
        step += 1
        await asyncio.sleep(interval_sec)
