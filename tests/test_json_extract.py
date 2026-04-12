import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from overwatch.analysis.json_extract import first_json_object, parse_model_json
from overwatch.models import ObservationsPass


class TestJsonExtract(unittest.TestCase):
    def test_first_json_object_plain(self) -> None:
        raw = '{"scene_summary": "x", "observations": []}'
        obj = first_json_object(f"prefix {raw} suffix")
        self.assertEqual(obj, json.loads(raw))

    def test_first_json_object_fence(self) -> None:
        text = 'Here:\n```json\n{"scene_summary": "s", "observations": [{"what": "a"}]}\n```\n'
        obj = first_json_object(text)
        self.assertEqual(obj["scene_summary"], "s")
        self.assertEqual(len(obj["observations"]), 1)

    def test_parse_observations(self) -> None:
        text = '{"scene_summary": "busy", "observations": [{"what": "forklift"}]}'
        m = parse_model_json(text, ObservationsPass)
        self.assertIsNotNone(m)
        assert m is not None
        self.assertEqual(m.scene_summary, "busy")
        self.assertEqual(m.observations[0].what, "forklift")


if __name__ == "__main__":
    unittest.main()
