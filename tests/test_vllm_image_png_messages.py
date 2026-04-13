import base64
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from overwatch.vllm_client import image_png_user_messages


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


if __name__ == "__main__":
    unittest.main()
