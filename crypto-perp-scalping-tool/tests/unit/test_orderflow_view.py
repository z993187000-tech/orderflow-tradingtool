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


if __name__ == "__main__":
    unittest.main()
