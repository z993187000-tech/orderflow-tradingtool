import unittest
from pathlib import Path

from crypto_perp_tool.web.orderflow import build_orderflow_view


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class OrderflowViewTests(unittest.TestCase):
    def test_build_orderflow_view_contains_dashboard_sections(self):
        view = build_orderflow_view(PROJECT_ROOT / "data" / "sample_trades.csv")

        self.assertEqual(view["summary"]["symbol"], "BTCUSDT")
        self.assertEqual(view["summary"]["trade_count"], 8)
        self.assertGreaterEqual(view["summary"]["signals"], 1)
        self.assertGreaterEqual(view["summary"]["orders"], 1)
        self.assertGreaterEqual(view["summary"]["closed_positions"], 1)
        self.assertGreater(len(view["trades"]), 0)
        self.assertGreater(len(view["delta_series"]), 0)
        self.assertTrue(any(level["type"] == "LVN" for level in view["profile_levels"]))
        self.assertTrue(any(marker["type"] == "signal" for marker in view["markers"]))
        self.assertTrue(any(marker["type"] == "position_closed" for marker in view["markers"]))

    def test_build_orderflow_view_splits_detail_metrics_by_mode_and_pnl_range(self):
        view = build_orderflow_view(PROJECT_ROOT / "data" / "sample_trades.csv")

        self.assertIn("mode_breakdown", view["summary"])
        self.assertIn("pnl_24h", view["summary"])
        self.assertIn("details", view)

        paper = view["details"]["paper"]
        live = view["details"]["live"]

        self.assertGreaterEqual(view["summary"]["mode_breakdown"]["paper"]["signals"], 1)
        self.assertGreaterEqual(view["summary"]["mode_breakdown"]["paper"]["orders"], 1)
        self.assertGreaterEqual(view["summary"]["mode_breakdown"]["paper"]["closed_positions"], 1)
        self.assertEqual(view["summary"]["mode_breakdown"]["live"]["signals"], 0)
        self.assertEqual(view["summary"]["mode_breakdown"]["live"]["orders"], 0)
        self.assertEqual(view["summary"]["mode_breakdown"]["live"]["closed_positions"], 0)

        self.assertGreaterEqual(len(paper["signals"]), 1)
        self.assertGreaterEqual(len(paper["orders"]), 1)
        self.assertGreaterEqual(len(paper["closed_positions"]), 1)
        self.assertGreater(paper["pnl_by_range"]["24h"], 0)
        self.assertGreater(paper["pnl_by_range"]["all"], 0)
        self.assertEqual(live["pnl_by_range"]["24h"], 0)
        self.assertEqual(view["summary"]["pnl_24h"], paper["pnl_by_range"]["24h"])


if __name__ == "__main__":
    unittest.main()
