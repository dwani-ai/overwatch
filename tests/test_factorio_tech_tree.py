import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from overwatch.factorio.tech_tree import load_tech_tree_text


class TestTechTreeLoad(unittest.TestCase):
    def test_missing_path(self) -> None:
        self.assertIsNone(load_tech_tree_text(None))
        self.assertIsNone(load_tech_tree_text(Path("/nonexistent/tech.json")))

    def test_json_file(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "t.json"
            p.write_text('{"milestones": ["a"]}', encoding="utf-8")
            t = load_tech_tree_text(p)
            self.assertIsNotNone(t)
            assert t is not None
            self.assertIn("milestones", t)


if __name__ == "__main__":
    unittest.main()
