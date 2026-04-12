from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from overwatch.models import AgentKind, AgentRunStatus, SourceType
from overwatch.store import open_store


class TestAgentRunsStore(unittest.IsolatedAsyncioTestCase):
    async def test_create_claim_finish(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            conn, store = await open_store(Path(d))
            try:
                job = await store.create_job(
                    source_type=SourceType.file,
                    source_path="/tmp/x.mp4",
                    meta={},
                )
                run = await store.create_agent_run(job.id, agent=AgentKind.synthesis, force=False)
                self.assertEqual(run.status, AgentRunStatus.pending)

                claimed = await store.claim_next_agent_run()
                self.assertIsNotNone(claimed)
                assert claimed is not None
                self.assertEqual(claimed.id, run.id)
                self.assertEqual(claimed.status, AgentRunStatus.processing)

                await store.finish_agent_run(
                    claimed.id,
                    status=AgentRunStatus.completed,
                    result={"ok": True},
                    event_id=42,
                    meta={"attempts": 1},
                )
                again = await store.get_agent_run(claimed.id)
                self.assertIsNotNone(again)
                assert again is not None
                self.assertEqual(again.status, AgentRunStatus.completed)
                self.assertEqual(again.result, {"ok": True})
                self.assertEqual(again.event_id, 42)
            finally:
                await conn.close()
