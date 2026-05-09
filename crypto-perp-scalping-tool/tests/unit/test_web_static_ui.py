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

    def test_summary_metrics_open_mode_split_details(self):
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

        self.assertIn('data-detail="signals"', html)
        self.assertIn('data-detail="orders"', html)
        self.assertIn('data-detail="pnl"', html)
        self.assertIn('id="detailPanel"', html)
        self.assertIn('data-range="24h"', html)
        self.assertIn("renderDetailPanel", js)
        self.assertIn("mode_breakdown", js)
        self.assertIn("pnl_by_range", js)

    def test_javascript_refreshes_dashboard_automatically(self):
        js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

        self.assertIn("setInterval(loadDashboard", js)

    def test_mobile_charts_have_bounded_css_height(self):
        css = (STATIC_DIR / "app.css").read_text(encoding="utf-8")
        js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

        self.assertIn("#priceCanvas", css)
        self.assertIn("clamp(", css)
        self.assertIn("rect.height", js)


if __name__ == "__main__":
    unittest.main()
