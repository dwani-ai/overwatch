from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from overwatch.agents.orchestration import notify_agent_orchestration_terminal
from overwatch.industry_pipelines import pipeline_for
from overwatch.models import AgentKind, AgentOrchestrationStatus, IndustryPack, JobStatus, SourceType
from overwatch.store import open_store


class TestOrchestrationStore(unittest.IsolatedAsyncioTestCase):
    async def test_start_creates_orch_and_first_run(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            conn, store = await open_store(Path(d))
            try:
                job = await store.create_job(
                    source_type=SourceType.file,
                    source_path="/tmp/x.mp4",
                    meta={},
                )
                await store.update_job_status(job.id, JobStatus.completed)
                orch, head = await store.start_agent_orchestration(
                    job.id,
                    [AgentKind.synthesis, AgentKind.risk_review],
                    force=False,
                )
                self.assertEqual(orch.status, AgentOrchestrationStatus.running)
                self.assertEqual(orch.current_step, 0)
                self.assertEqual(len(orch.steps), 2)
                self.assertEqual(head.agent, AgentKind.synthesis)
                self.assertEqual(head.meta.get("orch_step"), 0)
                self.assertEqual(head.meta.get("orchestration_id"), orch.id)
                self.assertIsNone(orch.industry_pack)
            finally:
                await conn.close()

    async def test_industry_pack_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            conn, store = await open_store(Path(d))
            try:
                job = await store.create_job(
                    source_type=SourceType.file,
                    source_path="/tmp/x.mp4",
                    meta={},
                )
                await store.update_job_status(job.id, JobStatus.completed)
                steps = pipeline_for(IndustryPack.retail_qsr)
                orch, head = await store.start_agent_orchestration(
                    job.id,
                    steps,
                    force=False,
                    industry_pack=IndustryPack.retail_qsr,
                )
                self.assertEqual(orch.industry_pack, IndustryPack.retail_qsr)
                again = await store.get_agent_orchestration(orch.id)
                self.assertIsNotNone(again)
                assert again is not None
                self.assertEqual(again.industry_pack, IndustryPack.retail_qsr)
                self.assertEqual(head.agent, steps[0])
            finally:
                await conn.close()

    async def test_notify_advances_to_second_run(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            conn, store = await open_store(Path(d))
            try:
                job = await store.create_job(
                    source_type=SourceType.file,
                    source_path="/tmp/x.mp4",
                    meta={},
                )
                await store.update_job_status(job.id, JobStatus.completed)
                orch, head = await store.start_agent_orchestration(
                    job.id,
                    [AgentKind.synthesis, AgentKind.risk_review],
                    force=False,
                )
                await notify_agent_orchestration_terminal(
                    store,
                    job.id,
                    head.meta,
                    success=True,
                )
                runs = await store.list_agent_runs_for_job(job.id, limit=10)
                self.assertEqual(len(runs), 2)
                agents = {r.agent for r in runs}
                self.assertEqual(agents, {AgentKind.synthesis, AgentKind.risk_review})
                again = await store.get_agent_orchestration(orch.id)
                self.assertIsNotNone(again)
                assert again is not None
                self.assertEqual(again.current_step, 1)
            finally:
                await conn.close()

    async def test_notify_completes_last_step(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            conn, store = await open_store(Path(d))
            try:
                job = await store.create_job(
                    source_type=SourceType.file,
                    source_path="/tmp/x.mp4",
                    meta={},
                )
                await store.update_job_status(job.id, JobStatus.completed)
                orch, head = await store.start_agent_orchestration(
                    job.id,
                    [AgentKind.synthesis],
                    force=False,
                )
                await notify_agent_orchestration_terminal(
                    store,
                    job.id,
                    head.meta,
                    success=True,
                )
                final = await store.get_agent_orchestration(orch.id)
                self.assertIsNotNone(final)
                assert final is not None
                self.assertEqual(final.status, AgentOrchestrationStatus.completed)
                self.assertEqual(final.current_step, 1)
            finally:
                await conn.close()

    async def test_notify_failure_marks_orch_failed(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            conn, store = await open_store(Path(d))
            try:
                job = await store.create_job(
                    source_type=SourceType.file,
                    source_path="/tmp/x.mp4",
                    meta={},
                )
                await store.update_job_status(job.id, JobStatus.completed)
                orch, head = await store.start_agent_orchestration(
                    job.id,
                    [AgentKind.synthesis, AgentKind.risk_review],
                    force=False,
                )
                await notify_agent_orchestration_terminal(
                    store,
                    job.id,
                    head.meta,
                    success=False,
                    error="boom",
                )
                final = await store.get_agent_orchestration(orch.id)
                self.assertIsNotNone(final)
                assert final is not None
                self.assertEqual(final.status, AgentOrchestrationStatus.failed)
                self.assertEqual(final.error, "boom")
                runs = await store.list_agent_runs_for_job(job.id, limit=10)
                self.assertEqual(len(runs), 1)
            finally:
                await conn.close()
