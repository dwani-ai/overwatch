from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from overwatch.agents.runner import process_agent_run
from overwatch.config import Settings
from overwatch.models import AgentKind, AgentRunStatus, JobStatus, SourceType, SynthesisAgentResult
from overwatch.store import open_store


class TestProcessAgentRunAdk(unittest.IsolatedAsyncioTestCase):
    async def test_process_agent_run_writes_event_payload(self) -> None:
        settings = Settings(vllm_base_url="http://test/v1", vllm_model="gemma4")
        ok = SynthesisAgentResult(
            executive_summary="Done.",
            key_observations=["a"],
            security_highlights=[],
            logistics_highlights=[],
            attendance_summary="",
            recommended_actions=[],
        )
        with tempfile.TemporaryDirectory() as d:
            conn, store = await open_store(Path(d))
            try:
                job = await store.create_job(
                    source_type=SourceType.file,
                    source_path="/tmp/x.mp4",
                    meta={},
                )
                await store.update_job_status(job.id, JobStatus.completed)
                await store.set_job_summary(job.id, {"schema_version": "1", "chunk_analyses": []})
                await store.create_agent_run(job.id, agent=AgentKind.synthesis, force=False)
                claimed = await store.claim_next_agent_run()
                self.assertIsNotNone(claimed)
                assert claimed is not None
                with patch(
                    "overwatch.agents.synthesis.run_job_agent_adk",
                    new_callable=AsyncMock,
                    return_value=(ok, {"attempts": 1, "truncated_input": False, "model": "gemma4"}),
                ):
                    await process_agent_run(store, settings, claimed)

                again = await store.get_agent_run(claimed.id)
                self.assertIsNotNone(again)
                assert again is not None
                self.assertEqual(again.status, AgentRunStatus.completed)
                self.assertIsNotNone(again.result)
                assert again.result is not None
                self.assertEqual(again.result.get("executive_summary"), "Done.")
            finally:
                await conn.close()
