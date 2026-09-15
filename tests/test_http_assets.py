import tempfile
import unittest
from pathlib import Path

from app import SiteHandler


class AssetCacheTests(unittest.TestCase):
    def cache_control(self, suffix, *, cache=True):
        with tempfile.TemporaryDirectory() as temporary:
            asset = Path(temporary) / f"asset{suffix}"
            asset.write_bytes(b"test")
            handler = SiteHandler.__new__(SiteHandler)
            handler._head_only = True
            headers = {}
            handler.send_response = lambda status: None
            handler.send_header = lambda name, value: headers.update({name: value})
            handler.end_headers = lambda: None
            handler._send_file(asset, cache=cache)
            return headers["Cache-Control"]

    def test_scripts_and_styles_revalidate_after_deployment(self):
        for suffix in (".js", ".css"):
            with self.subTest(suffix=suffix):
                self.assertEqual(self.cache_control(suffix), "no-cache")

    def test_images_can_still_be_cached(self):
        self.assertEqual(self.cache_control(".png"), "public, max-age=3600")

    def test_spa_html_is_not_cached(self):
        self.assertEqual(self.cache_control(".html", cache=False), "no-cache")


if __name__ == "__main__":
    unittest.main()
