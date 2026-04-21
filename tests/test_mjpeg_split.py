"""Tests ``split_mjpeg_stream`` without importing the full ``overwatch`` package (avoids heavy deps)."""

import importlib.util
import sys
import unittest
from pathlib import Path


def _load_jpeg_frames_module():
    root = Path(__file__).resolve().parents[1]
    path = root / "src/overwatch/video/jpeg_frames.py"
    name = "overwatch_jpeg_frames_testmod"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_jf = _load_jpeg_frames_module()
split_mjpeg_stream = _jf.split_mjpeg_stream


def _minimal_jpeg() -> bytes:
    return b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9"


class TestSplitMjpegStream(unittest.TestCase):
    def test_empty(self) -> None:
        self.assertEqual(split_mjpeg_stream(b""), [])

    def test_single_frame(self) -> None:
        j = _minimal_jpeg()
        self.assertEqual(split_mjpeg_stream(j), [j])

    def test_two_concatenated(self) -> None:
        a = _minimal_jpeg()
        b = _minimal_jpeg()
        self.assertEqual(split_mjpeg_stream(a + b), [a, b])


if __name__ == "__main__":
    unittest.main()
