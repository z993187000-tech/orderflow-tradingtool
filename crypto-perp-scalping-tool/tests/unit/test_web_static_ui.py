import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = PROJECT_ROOT / "src" / "crypto_perp_tool" / "web" / "static"


class WebStaticUiTests(unittest.TestCase):
    def test_index_contains_chinese_metric_labels(self):
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

        self.assertIn("最新价", html)
        self.assertIn("累计Delta", html)
        self.assertIn("连接状态", html)
        self.assertIn("成交明细", html)

    def test_javascript_draws_y_axis_labels(self):
        js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

        self.assertIn("drawYAxis", js)
        self.assertIn("formatAxisValue", js)


if __name__ == "__main__":
    unittest.main()
