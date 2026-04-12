from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

from overwatch.db import connect
from overwatch.models import AgentTrack, EventRecord, JobRecord, JobStatus, SourceType


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


class JobStore:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def create_job(
        self,
        *,
        source_type: SourceType,
        source_path: str,
        meta: dict[str, Any] | None = None,
    ) -> JobRecord:
        job_id = str(uuid.uuid4())
        now = _iso(datetime.now(timezone.utc))
        meta = meta or {}
        await self._conn.execute(
            """
            INSERT INTO jobs (id, source_type, source_path, status, created_at, updated_at, error, meta_json)
            VALUES (?, ?, ?, ?, ?, ?, NULL, ?)
            """,
            (
                job_id,
                source_type.value,
                source_path,
                JobStatus.pending.value,
                now,
                now,
                json.dumps(meta),
            ),
        )
        await self._conn.commit()
        return await self.get_job(job_id)

    async def get_job(self, job_id: str) -> JobRecord:
        cur = await self._conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
        row = await cur.fetchone()
        if row is None:
            raise KeyError(job_id)
        return self._row_to_job(row)

    async def list_jobs(self, *, limit: int = 50) -> list[JobRecord]:
        cur = await self._conn.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        rows = await cur.fetchall()
        return [self._row_to_job(r) for r in rows]

    async def update_job_status(
        self,
        job_id: str,
        status: JobStatus,
        *,
        error: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> None:
        now = _iso(datetime.now(timezone.utc))
        if meta is not None:
            await self._conn.execute(
                """
                UPDATE jobs SET status = ?, updated_at = ?, error = ?, meta_json = ?
                WHERE id = ?
                """,
                (status.value, now, error, json.dumps(meta), job_id),
            )
        elif status == JobStatus.completed:
            await self._conn.execute(
                "UPDATE jobs SET status = ?, updated_at = ?, error = NULL WHERE id = ?",
                (status.value, now, job_id),
            )
        else:
            await self._conn.execute(
                """
                UPDATE jobs SET status = ?, updated_at = ?, error = COALESCE(?, error)
                WHERE id = ?
                """,
                (status.value, now, error, job_id),
            )
        await self._conn.commit()

    async def merge_job_meta(self, job_id: str, patch: dict[str, Any]) -> None:
        job = await self.get_job(job_id)
        merged = {**job.meta, **patch}
        now = _iso(datetime.now(timezone.utc))
        await self._conn.execute(
            "UPDATE jobs SET meta_json = ?, updated_at = ? WHERE id = ?",
            (json.dumps(merged), now, job_id),
        )
        await self._conn.commit()

    async def append_event(
        self,
        job_id: str,
        *,
        agent: AgentTrack,
        event_type: str,
        payload: dict[str, Any],
        observed_at: datetime | None = None,
        frame_index: int | None = None,
        pts_ms: int | None = None,
        severity: str | None = None,
    ) -> int:
        ts = observed_at or datetime.now(timezone.utc)
        cur = await self._conn.execute(
            """
            INSERT INTO events (job_id, observed_at, frame_index, pts_ms, agent, event_type, severity, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                _iso(ts),
                frame_index,
                pts_ms,
                agent.value,
                event_type,
                severity,
                json.dumps(payload),
            ),
        )
        await self._conn.commit()
        return int(cur.lastrowid)

    async def list_events(self, job_id: str) -> list[EventRecord]:
        cur = await self._conn.execute(
            "SELECT * FROM events WHERE job_id = ? ORDER BY id ASC",
            (job_id,),
        )
        rows = await cur.fetchall()
        return [self._row_to_event(r) for r in rows]

    async def has_active_job_for_path(self, path: str) -> bool:
        cur = await self._conn.execute(
            """
            SELECT 1 FROM jobs
            WHERE source_path = ? AND status IN (?, ?)
            LIMIT 1
            """,
            (path, JobStatus.pending.value, JobStatus.processing.value),
        )
        return await cur.fetchone() is not None

    async def next_pending_job(self) -> JobRecord | None:
        cur = await self._conn.execute(
            """
            SELECT * FROM jobs WHERE status = ? ORDER BY created_at ASC LIMIT 1
            """,
            (JobStatus.pending.value,),
        )
        row = await cur.fetchone()
        return None if row is None else self._row_to_job(row)

    async def get_processed_fingerprint(self, path: str) -> str | None:
        cur = await self._conn.execute(
            "SELECT fingerprint FROM processed_files WHERE path = ?",
            (path,),
        )
        row = await cur.fetchone()
        return None if row is None else str(row["fingerprint"])

    async def record_processed_file(self, path: str, fingerprint: str, job_id: str) -> None:
        await self._conn.execute(
            """
            INSERT OR REPLACE INTO processed_files (path, fingerprint, job_id) VALUES (?, ?, ?)
            """,
            (path, fingerprint, job_id),
        )
        await self._conn.commit()

    def _row_to_job(self, row: aiosqlite.Row) -> JobRecord:
        return JobRecord(
            id=str(row["id"]),
            source_type=SourceType(str(row["source_type"])),
            source_path=str(row["source_path"]),
            status=JobStatus(str(row["status"])),
            created_at=_parse_iso(str(row["created_at"])),
            updated_at=_parse_iso(str(row["updated_at"])),
            error=row["error"],
            meta=json.loads(row["meta_json"] or "{}"),
        )

    def _row_to_event(self, row: aiosqlite.Row) -> EventRecord:
        return EventRecord(
            id=int(row["id"]),
            job_id=str(row["job_id"]),
            observed_at=_parse_iso(str(row["observed_at"])),
            frame_index=row["frame_index"],
            pts_ms=row["pts_ms"],
            agent=AgentTrack(str(row["agent"])),
            event_type=str(row["event_type"]),
            severity=row["severity"],
            payload=json.loads(row["payload_json"] or "{}"),
        )


async def open_store(data_dir: Path) -> tuple[aiosqlite.Connection, JobStore]:
    data_dir.mkdir(parents=True, exist_ok=True)
    conn = await connect(data_dir / "overwatch.db")
    return conn, JobStore(conn)
