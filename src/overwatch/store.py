from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import aiosqlite

from overwatch.db import connect
from overwatch.models import (
    AgentKind,
    AgentOrchestrationOut,
    AgentOrchestrationStatus,
    AgentRunOut,
    AgentRunStatus,
    AgentTrack,
    EventRecord,
    IndustryPack,
    JobRecord,
    JobStatus,
    SourceType,
)


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

    async def set_job_summary(self, job_id: str, summary: dict[str, Any]) -> None:
        now = _iso(datetime.now(timezone.utc))
        await self._conn.execute(
            "UPDATE jobs SET summary_json = ?, updated_at = ? WHERE id = ?",
            (json.dumps(summary), now, job_id),
        )
        await self._conn.commit()

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

    async def delete_job(self, job_id: str) -> bool:
        """Delete a job and all its related rows. Returns False if not found."""
        cur = await self._conn.execute("SELECT 1 FROM jobs WHERE id = ?", (job_id,))
        if await cur.fetchone() is None:
            return False
        await self._conn.execute("DELETE FROM events WHERE job_id = ?", (job_id,))
        await self._conn.execute("DELETE FROM agent_runs WHERE job_id = ?", (job_id,))
        await self._conn.execute("DELETE FROM agent_orchestrations WHERE job_id = ?", (job_id,))
        await self._conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        await self._conn.commit()
        return True

    async def list_events(self, job_id: str) -> list[EventRecord]:
        cur = await self._conn.execute(
            "SELECT * FROM events WHERE job_id = ? ORDER BY id ASC",
            (job_id,),
        )
        rows = await cur.fetchall()
        return [self._row_to_event(r) for r in rows]

    async def get_latest_event(
        self,
        job_id: str,
        *,
        agent: AgentTrack | None = None,
        event_type: str | None = None,
    ) -> EventRecord | None:
        clauses: list[str] = ["job_id = ?"]
        params: list[Any] = [job_id]
        if agent is not None:
            clauses.append("agent = ?")
            params.append(agent.value)
        if event_type is not None:
            clauses.append("event_type = ?")
            params.append(event_type)
        where = " AND ".join(clauses)
        cur = await self._conn.execute(
            f"SELECT * FROM events WHERE {where} ORDER BY id DESC LIMIT 1",
            params,
        )
        row = await cur.fetchone()
        return None if row is None else self._row_to_event(row)

    async def list_events_page(
        self,
        job_id: str,
        *,
        after_id: int = 0,
        limit: int = 50,
    ) -> list[EventRecord]:
        lim = max(1, min(limit, 200))
        cur = await self._conn.execute(
            """
            SELECT * FROM events
            WHERE job_id = ? AND id > ?
            ORDER BY id ASC
            LIMIT ?
            """,
            (job_id, after_id, lim),
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

    async def create_agent_run(
        self,
        job_id: str,
        *,
        agent: AgentKind,
        force: bool = False,
        meta: dict[str, Any] | None = None,
    ) -> AgentRunOut:
        run_id = str(uuid.uuid4())
        now = _iso(datetime.now(timezone.utc))
        meta_json = json.dumps(meta or {})
        await self._conn.execute(
            """
            INSERT INTO agent_runs (id, job_id, agent, status, force_run, created_at, updated_at, error, result_json, event_id, meta_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?)
            """,
            (
                run_id,
                job_id,
                agent.value,
                AgentRunStatus.pending.value,
                1 if force else 0,
                now,
                now,
                meta_json,
            ),
        )
        await self._conn.commit()
        row = await self._fetch_agent_run_row(run_id)
        assert row is not None
        return self._row_to_agent_run(row)

    async def job_has_active_agent_orchestration(self, job_id: str) -> bool:
        cur = await self._conn.execute(
            """
            SELECT 1 FROM agent_orchestrations
            WHERE job_id = ? AND status = ?
            LIMIT 1
            """,
            (job_id, AgentOrchestrationStatus.running.value),
        )
        return await cur.fetchone() is not None

    async def start_agent_orchestration(
        self,
        job_id: str,
        steps: list[AgentKind],
        *,
        force: bool = False,
        industry_pack: IndustryPack | None = None,
    ) -> tuple[AgentOrchestrationOut, AgentRunOut]:
        """
        Create orchestration row and enqueue the **first** step in one transaction.
        """
        if not steps:
            raise ValueError("steps must be non-empty")
        orch_id = str(uuid.uuid4())
        run_id = str(uuid.uuid4())
        now = _iso(datetime.now(timezone.utc))
        step_values = [s.value for s in steps]
        steps_json = json.dumps(step_values)
        first = steps[0]
        orch_meta = {
            "orchestration_id": orch_id,
            "orch_step": 0,
            "orch_steps": step_values,
        }
        run_meta_json = json.dumps(orch_meta)
        await self._conn.execute("BEGIN IMMEDIATE")
        try:
            await self._conn.execute(
                """
                INSERT INTO agent_orchestrations (
                    id, job_id, status, steps_json, current_step, force_run, industry_pack, error, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
                """,
                (
                    orch_id,
                    job_id,
                    AgentOrchestrationStatus.running.value,
                    steps_json,
                    0,
                    1 if force else 0,
                    industry_pack.value if industry_pack is not None else None,
                    now,
                    now,
                ),
            )
            await self._conn.execute(
                """
                INSERT INTO agent_runs (id, job_id, agent, status, force_run, created_at, updated_at, error, result_json, event_id, meta_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?)
                """,
                (
                    run_id,
                    job_id,
                    first.value,
                    AgentRunStatus.pending.value,
                    1 if force else 0,
                    now,
                    now,
                    run_meta_json,
                ),
            )
            await self._conn.commit()
        except Exception:
            await self._conn.rollback()
            raise
        orch_row = await self._fetch_agent_orch_row(orch_id)
        run_row = await self._fetch_agent_run_row(run_id)
        assert orch_row is not None and run_row is not None
        return self._row_to_agent_orch(orch_row), self._row_to_agent_run(run_row)

    async def get_agent_orchestration(self, orch_id: str) -> AgentOrchestrationOut | None:
        row = await self._fetch_agent_orch_row(orch_id)
        return None if row is None else self._row_to_agent_orch(row)

    async def list_agent_orchestrations_for_job(
        self,
        job_id: str,
        *,
        limit: int = 20,
    ) -> list[AgentOrchestrationOut]:
        lim = max(1, min(limit, 50))
        cur = await self._conn.execute(
            """
            SELECT * FROM agent_orchestrations
            WHERE job_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (job_id, lim),
        )
        rows = await cur.fetchall()
        return [self._row_to_agent_orch(r) for r in rows]

    async def update_agent_orchestration_step(self, orch_id: str, *, current_step: int) -> None:
        now = _iso(datetime.now(timezone.utc))
        await self._conn.execute(
            """
            UPDATE agent_orchestrations SET current_step = ?, updated_at = ? WHERE id = ?
            """,
            (current_step, now, orch_id),
        )
        await self._conn.commit()

    async def complete_agent_orchestration(self, orch_id: str) -> None:
        now = _iso(datetime.now(timezone.utc))
        cur = await self._conn.execute(
            "SELECT steps_json FROM agent_orchestrations WHERE id = ?",
            (orch_id,),
        )
        row = await cur.fetchone()
        if row is None:
            return
        n = len(json.loads(row["steps_json"] or "[]"))
        await self._conn.execute(
            """
            UPDATE agent_orchestrations
            SET status = ?, current_step = ?, updated_at = ?, error = NULL
            WHERE id = ? AND status = ?
            """,
            (
                AgentOrchestrationStatus.completed.value,
                n,
                now,
                orch_id,
                AgentOrchestrationStatus.running.value,
            ),
        )
        await self._conn.commit()

    async def fail_agent_orchestration(self, orch_id: str, error: str) -> None:
        now = _iso(datetime.now(timezone.utc))
        await self._conn.execute(
            """
            UPDATE agent_orchestrations
            SET status = ?, updated_at = ?, error = ?
            WHERE id = ? AND status = ?
            """,
            (
                AgentOrchestrationStatus.failed.value,
                now,
                error,
                orch_id,
                AgentOrchestrationStatus.running.value,
            ),
        )
        await self._conn.commit()

    async def _fetch_agent_orch_row(self, orch_id: str) -> aiosqlite.Row | None:
        cur = await self._conn.execute("SELECT * FROM agent_orchestrations WHERE id = ?", (orch_id,))
        return await cur.fetchone()

    def _row_to_agent_orch(self, row: aiosqlite.Row) -> AgentOrchestrationOut:
        raw_steps = row["steps_json"]
        step_strs: list[str] = json.loads(raw_steps) if raw_steps else []
        ind: IndustryPack | None = None
        try:
            raw_ind = row["industry_pack"]
        except (KeyError, IndexError, TypeError):
            raw_ind = None
        if raw_ind:
            try:
                ind = IndustryPack(str(raw_ind))
            except ValueError:
                ind = None
        return AgentOrchestrationOut(
            id=str(row["id"]),
            job_id=str(row["job_id"]),
            status=AgentOrchestrationStatus(str(row["status"])),
            steps=[AgentKind(s) for s in step_strs],
            current_step=int(row["current_step"]),
            force=bool(row["force_run"]),
            industry_pack=ind,
            error=row["error"],
            created_at=_parse_iso(str(row["created_at"])),
            updated_at=_parse_iso(str(row["updated_at"])),
        )

    async def get_agent_run(self, run_id: str) -> AgentRunOut | None:
        row = await self._fetch_agent_run_row(run_id)
        return None if row is None else self._row_to_agent_run(row)

    async def list_agent_runs_for_job(self, job_id: str, *, limit: int = 30) -> list[AgentRunOut]:
        lim = max(1, min(limit, 100))
        cur = await self._conn.execute(
            """
            SELECT * FROM agent_runs WHERE job_id = ? ORDER BY created_at DESC LIMIT ?
            """,
            (job_id, lim),
        )
        rows = await cur.fetchall()
        return [self._row_to_agent_run(r) for r in rows]

    async def claim_next_agent_run(self) -> AgentRunOut | None:
        """Atomically pick the oldest pending run and mark it ``processing`` (safe with concurrent API workers)."""
        now = _iso(datetime.now(timezone.utc))
        await self._conn.execute("BEGIN IMMEDIATE")
        try:
            cur = await self._conn.execute(
                """
                UPDATE agent_runs
                SET status = ?, updated_at = ?
                WHERE id = (
                    SELECT id FROM agent_runs
                    WHERE status = ?
                    ORDER BY created_at ASC
                    LIMIT 1
                )
                AND status = ?
                RETURNING *
                """,
                (
                    AgentRunStatus.processing.value,
                    now,
                    AgentRunStatus.pending.value,
                    AgentRunStatus.pending.value,
                ),
            )
            row = await cur.fetchone()
            await self._conn.commit()
        except Exception:
            await self._conn.rollback()
            raise
        return None if row is None else self._row_to_agent_run(row)

    async def finish_agent_run(
        self,
        run_id: str,
        *,
        status: AgentRunStatus,
        result: dict[str, Any] | None = None,
        error: str | None = None,
        event_id: int | None = None,
        meta: dict[str, Any] | None = None,
    ) -> None:
        now = _iso(datetime.now(timezone.utc))
        meta_json = json.dumps(meta or {})
        result_json = json.dumps(result) if result is not None else None
        await self._conn.execute(
            """
            UPDATE agent_runs
            SET status = ?, updated_at = ?, error = ?, result_json = ?, event_id = ?, meta_json = ?
            WHERE id = ?
            """,
            (status.value, now, error, result_json, event_id, meta_json, run_id),
        )
        await self._conn.commit()

    async def fail_stale_agent_runs(self, *, older_than_sec: float) -> int:
        """
        Mark ``processing`` runs whose ``updated_at`` is older than ``older_than_sec`` as ``failed``.
        Covers worker crashes and deploys where ``finish_agent_run`` never ran.
        """
        now_dt = datetime.now(timezone.utc)
        cutoff = now_dt - timedelta(seconds=older_than_sec)
        cutoff_iso = _iso(cutoff)
        now = _iso(now_dt)
        msg = (
            "Stale agent run: remained in processing too long "
            "(worker restart, hang, or timeout). Re-queue with a new run if needed."
        )
        cur = await self._conn.execute(
            """
            UPDATE agent_runs
            SET status = ?, updated_at = ?, error = ?
            WHERE status = ? AND updated_at < ?
            """,
            (AgentRunStatus.failed.value, now, msg, AgentRunStatus.processing.value, cutoff_iso),
        )
        await self._conn.commit()
        rc = cur.rowcount
        return int(rc) if rc is not None and rc >= 0 else 0

    async def _fetch_agent_run_row(self, run_id: str) -> aiosqlite.Row | None:
        cur = await self._conn.execute("SELECT * FROM agent_runs WHERE id = ?", (run_id,))
        return await cur.fetchone()

    def _row_to_agent_run(self, row: aiosqlite.Row) -> AgentRunOut:
        raw_result = row["result_json"]
        result = json.loads(raw_result) if raw_result else None
        return AgentRunOut(
            id=str(row["id"]),
            job_id=str(row["job_id"]),
            agent=AgentKind(str(row["agent"])),
            status=AgentRunStatus(str(row["status"])),
            force=bool(row["force_run"]),
            created_at=_parse_iso(str(row["created_at"])),
            updated_at=_parse_iso(str(row["updated_at"])),
            error=row["error"],
            result=result,
            event_id=row["event_id"],
            meta=json.loads(row["meta_json"] or "{}"),
        )

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
        try:
            raw_summary = row["summary_json"]
        except (KeyError, IndexError, TypeError):
            raw_summary = None
        summary = json.loads(raw_summary) if raw_summary else None
        return JobRecord(
            id=str(row["id"]),
            source_type=SourceType(str(row["source_type"])),
            source_path=str(row["source_path"]),
            status=JobStatus(str(row["status"])),
            created_at=_parse_iso(str(row["created_at"])),
            updated_at=_parse_iso(str(row["updated_at"])),
            error=row["error"],
            meta=json.loads(row["meta_json"] or "{}"),
            summary=summary,
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
