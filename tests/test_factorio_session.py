import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from overwatch.factorio.session import FactorioSessionStore


class TestFactorioSession(unittest.TestCase):
    def test_create_append_list(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "f"
            store = FactorioSessionStore(root)
            try:
                sid = store.create_session(meta={"note": "t"})
                png = b"\x89PNG\r\n\x1a\nfake"
                rec = store.append_frame(sid, 0, png)
                self.assertEqual(rec.step_index, 0)
                self.assertTrue((root / rec.rel_path).is_file())
                self.assertEqual((root / rec.rel_path).read_bytes(), png)
                rows = store.list_frames(sid)
                self.assertEqual(len(rows), 1)
                self.assertTrue(store.has_session(sid))
                store.append_agent_step(
                    sid,
                    0,
                    frame_rel_path=rec.rel_path,
                    state_json='{"schema_version":"1","confidence":0.5}',
                    action_json='{"type":"noop"}',
                    planner_raw_text="t",
                    error=None,
                )
                asteps = store.list_agent_steps(sid)
                self.assertEqual(len(asteps), 1)
                self.assertEqual(asteps[0].action_json, '{"type":"noop"}')
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
