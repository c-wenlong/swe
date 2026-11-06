"""Link previews fetch og:image and og:url without the page's base, so the
landing pages that set them must use absolute URLs."""

import re
import unittest
from pathlib import Path

LANDING = Path(__file__).resolve().parents[1] / "app" / "landing-page"


class TestLandingOpenGraph(unittest.TestCase):
    def test_image_and_url_are_absolute(self):
        for page in (LANDING / "index.html", LANDING / "install" / "index.html"):
            html = page.read_text(encoding="utf-8")
            for prop in ("og:image", "og:url"):
                found = re.findall(r'<meta property="%s" content="([^"]*)">' % prop, html)
                self.assertEqual(len(found), 1, (page.name, prop))
                self.assertTrue(found[0].startswith("https://"), (page.name, prop, found[0]))


if __name__ == "__main__":
    unittest.main()
