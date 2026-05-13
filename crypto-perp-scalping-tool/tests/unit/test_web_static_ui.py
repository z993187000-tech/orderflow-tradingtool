import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = PROJECT_ROOT / "src" / "crypto_perp_tool" / "web" / "static"


class WebStaticUiTests(unittest.TestCase):
    def test_index_contains_chinese_metric_labels(self):
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

        self.assertIn("最新成交价", html)
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

    def test_recent_tape_filters_to_large_trades(self):
        js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

        self.assertIn("LARGE_TAPE_MIN_QTY", js)
        self.assertIn("largeTapeTrades", js)
        self.assertIn("trade.quantity >= LARGE_TAPE_MIN_QTY", js)

    def test_mobile_charts_have_bounded_css_height(self):
        css = (STATIC_DIR / "app.css").read_text(encoding="utf-8")
        js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

        self.assertIn("#priceCanvas", css)
        self.assertIn("clamp(", css)
        self.assertIn("rect.height", js)

    def test_summary_shows_perp_trade_and_reference_price_context(self):
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

        self.assertIn("永续最新成交价", html)
        self.assertIn('id="lastPriceMeta"', html)
        self.assertIn("spot_last_price", js)
        self.assertIn("mark_price", js)
        self.assertIn("index_price", js)
        self.assertIn("last_trade_price", js)

    def test_summary_shows_paper_operator_context(self):
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

        for element_id in ["currentPosition", "signalReasons", "rejectReasons", "dataLag", "streamFreshness", "lastTradeTime"]:
            self.assertIn(f'id="{element_id}"', html)
            self.assertIn(element_id, js)

        self.assertIn("open_position", js)
        self.assertIn("signal_reasons", js)
        self.assertIn("reject_reasons", js)
        self.assertIn("data_lag_ms", js)
        self.assertIn("exchange_lag_ms", js)
        self.assertIn("stream_freshness_ms", js)
        self.assertIn("last_trade_time", js)

    def test_dashboard_splits_exchange_lag_and_stream_freshness_labels(self):
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

        self.assertIn("Exchange Lag", html)
        self.assertIn("Stream Freshness", html)
        self.assertIn("exchange_lag_ms", js)
        self.assertIn("stream_freshness_ms", js)

    def test_price_chart_draws_aggression_bubble_markers(self):
        js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

        self.assertIn("aggression_bubble", js)
        self.assertIn("drawAggressionBubble", js)
        self.assertIn("marker.quantity", js)
        self.assertIn("marker.side", js)

    def test_price_chart_deduplicates_profile_level_labels(self):
        js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

        self.assertIn("latestProfileLevels", js)
        self.assertIn("selectedProfileLevels", js)
        self.assertIn("touched_at", js)
        self.assertNotIn("maxHvnLvn", js)

    def test_price_chart_embeds_profile_overlay_on_price_axis(self):
        js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

        self.assertIn("PROFILE_OVERLAY_WIDTH", js)
        self.assertIn("const chartRight = canvas.width", js)
        self.assertIn("drawVolumeProfileOverlay", js)
        self.assertIn("scale.y(level.price)", js)
        self.assertIn("canvas.width - PROFILE_OVERLAY_WIDTH", js)
        self.assertNotIn("canvas.width - histogramWidth", js)

    def test_price_chart_draws_seeded_klines_without_trades(self):
        js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

        self.assertIn("hasPriceData", js)
        self.assertIn("safeKlines.length", js)
        self.assertNotIn("if (!trades.length) return;", js)

    def test_dashboard_renders_strategy_explainability_panel(self):
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
        css = (STATIC_DIR / "app.css").read_text(encoding="utf-8")

        for element_id in [
            "lastBreakEvenShift",
            "lastAbsorptionReduce",
            "lastAggressionBubble",
            "atrState",
            "cvdDivergence",
        ]:
            self.assertIn(f'id="{element_id}"', html)
            self.assertIn(element_id, js)

        self.assertIn("renderStrategyState", js)
        self.assertIn("last_break_even_shift", js)
        self.assertIn("last_absorption_reduce", js)
        self.assertIn("last_aggression_bubble", js)
        self.assertIn("cvd_divergence", js)
        self.assertIn(".strategy-state", css)

    def test_static_dashboard_text_is_not_mojibake(self):
        text = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        text += (STATIC_DIR / "app.js").read_text(encoding="utf-8")

        self.assertNotIn("妯℃嫙", text)
        self.assertNotIn("瀹炵洏", text)
        self.assertNotIn("鏃堕棿", text)


if __name__ == "__main__":
    unittest.main()
