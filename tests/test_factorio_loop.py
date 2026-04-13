import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from overwatch.factorio.loop import capture_loop
from overwatch.factorio.session import FactorioSessionStore


class TestCaptureLoop(unittest.TestCase):
    def test_loop_uses_capture_fn(self) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory() as d:
                root = Path(d) / "f"
                store = FactorioSessionStore(root)
                try:
                    sid = store.create_session()
                    fake = b"\x89PNG\r\n\x1a\nloop"

                    async def go() -> int:
                        n = 0
                        async for _step in capture_loop(
                            store,
                            sid,
                            interval_sec=0.01,
                            max_frames=2,
                            capture_fn=lambda: fake,
                        ):
                            n += 1
                        return n

                    n = await go()
                    self.assertEqual(n, 2)
                    self.assertEqual(len(store.list_frames(sid)), 2)
                    self.assertEqual(store.list_frames(sid)[0].bytes_len, len(fake))
                finally:
                    store.close()

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
