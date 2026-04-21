import base64
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from overwatch.vllm_client import chunk_jpeg_frames_user_messages, image_png_user_messages


class TestImagePngUserMessages(unittest.TestCase):
    def test_builds_data_uri(self) -> None:
        png = b"\x89PNG\r\n\x1a\n"
        msgs = image_png_user_messages(instruction="read hud", png_bytes=png)
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["role"], "user")
        content = msgs[0]["content"]
        self.assertEqual(content[0], {"type": "text", "text": "read hud"})
        url = content[1]["image_url"]["url"]
        self.assertTrue(url.startswith("data:image/png;base64,"))
        rest = url.split(",", 1)[1]
        self.assertEqual(base64.standard_b64decode(rest), png)


class TestChunkJpegFramesUserMessages(unittest.TestCase):
    def test_multiple_image_url_parts(self) -> None:
        j1 = b"\xff\xd8\xff\xd9"
        j2 = b"\xff\xd8\x00\xff\xd9"
        msgs = chunk_jpeg_frames_user_messages(instruction="look", jpeg_frames=[j1, j2])
        content = msgs[0]["content"]
        self.assertEqual(content[0], {"type": "text", "text": "look"})
        self.assertEqual(content[1]["type"], "image_url")
        self.assertTrue(content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,"))
        self.assertEqual(content[2]["type"], "image_url")


if __name__ == "__main__":
    unittest.main()
