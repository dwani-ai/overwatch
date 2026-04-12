from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from overwatch.config import Settings
from overwatch.models import SourceType
from overwatch.store import JobStore

logger = logging.getLogger(__name__)

IGNORE_SUFFIXES = frozenset({".tmp", ".part", ".crdownload", ".partial"})


@dataclass
class _Pending:
    size: int
    mtime_ns: int
    stable_since: float


@dataclass
class FolderIngest:
    settings: Settings
    store: JobStore
    _pending: dict[str, _Pending] = field(default_factory=dict)

    def _is_video_file(self, path: Path) -> bool:
        if not path.is_file():
            return False
        name = path.name.lower()
        if name.startswith("."):
            return False
        for ign in IGNORE_SUFFIXES:
            if name.endswith(ign):
                return False
        return path.suffix.lower() in self.settings.ingest_suffixes

    def _fingerprint(self, st: os.stat_result) -> str:
        mtime_ns = getattr(st, "st_mtime_ns", None)
        if mtime_ns is None:
            mtime_ns = int(st.st_mtime * 1e9)
        return f"{st.st_size}:{mtime_ns}"

    async def scan_once(self) -> int:
        """Return number of new jobs enqueued."""
        base = self.settings.ingest_dir.resolve()
        if not base.is_dir():
            base.mkdir(parents=True, exist_ok=True)
            return 0

        now = time.monotonic()
        enqueued = 0

        seen_paths: set[str] = set()
        for path in base.rglob("*"):
            if not path.is_file() or not self._is_video_file(path):
                continue
            try:
                st = path.stat()
            except OSError:
                continue
            key = str(path.resolve())
            seen_paths.add(key)
            fp = self._fingerprint(st)

            done_fp = await self.store.get_processed_fingerprint(key)
            if done_fp == fp:
                self._pending.pop(key, None)
                continue

            if await self.store.has_active_job_for_path(key):
                self._pending.pop(key, None)
                continue

            size, mtime_ns = st.st_size, getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9))
            pend = self._pending.get(key)
            if pend is None or pend.size != size or pend.mtime_ns != mtime_ns:
                self._pending[key] = _Pending(size=size, mtime_ns=mtime_ns, stable_since=now)
                continue

            if now - pend.stable_since < self.settings.ingest_stable_sec:
                continue

            job = await self.store.create_job(
                source_type=SourceType.file,
                source_path=key,
                meta={"fingerprint": fp, "ingest": "folder"},
            )
            self._pending.pop(key, None)
            enqueued += 1
            logger.info("Enqueued job %s for %s", job.id, key)

        for key in list(self._pending.keys()):
            if key not in seen_paths:
                self._pending.pop(key, None)

        return enqueued

