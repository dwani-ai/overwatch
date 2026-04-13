import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from overwatch.factorio.capture import png_screen_dimensions


class TestPngDimensions(unittest.TestCase):
    def test_valid_png(self) -> None:
        # 1x1 RGB PNG (same generator as eval fixture)
        import struct
        import zlib

        w, h = 1, 1
        rgb = (200, 100, 50)
        rows = [b"\x00" + bytes(rgb) * w for _ in range(h)]
        raw = b"".join(rows)
        comp = zlib.compress(raw, 9)

        def chunk(tag: bytes, data: bytes) -> bytes:
            crc = zlib.crc32(tag + data) & 0xFFFFFFFF
            return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

        ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
        png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", comp) + chunk(b"IEND", b"")
        d = png_screen_dimensions(png)
        self.assertEqual(d, (1, 1))

    def test_garbage(self) -> None:
        self.assertIsNone(png_screen_dimensions(b"not a png"))


if __name__ == "__main__":
    unittest.main()
