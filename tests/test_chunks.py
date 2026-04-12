import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from overwatch.models import ChunkPlanItem  # noqa: E402
from overwatch.video.chunks import plan_chunks  # noqa: E402
from overwatch.video.probe import VideoProbe  # noqa: E402


class TestChunks(unittest.TestCase):
    def test_plan_chunks_single_segment_under_cap(self) -> None:
        probe = VideoProbe(
            duration_sec=10.0,
            avg_frame_rate=25.0,
            width=1920,
            height=1080,
            codec="h264",
        )
        chunks = plan_chunks(probe, target_fps=1.0, max_chunk_sec=60.0)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].chunk_index, 0)
        self.assertEqual(chunks[0].start_frame, 0)
        self.assertGreaterEqual(chunks[0].end_frame, 249)

    def test_plan_chunks_splits_long_video(self) -> None:
        probe = VideoProbe(
            duration_sec=125.0,
            avg_frame_rate=25.0,
            width=1920,
            height=1080,
            codec="h264",
        )
        chunks = plan_chunks(probe, max_chunk_sec=60.0)
        self.assertGreaterEqual(len(chunks), 2)
        self.assertIsInstance(chunks[0], ChunkPlanItem)


if __name__ == "__main__":
    unittest.main()
