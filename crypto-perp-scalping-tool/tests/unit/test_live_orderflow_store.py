import unittest

from crypto_perp_tool.market_data import TradeEvent
from crypto_perp_tool.web.live_store import LiveOrderflowStore


class LiveOrderflowStoreTests(unittest.TestCase):
    def test_live_store_builds_orderflow_view_from_recent_events(self):
        store = LiveOrderflowStore(symbol="BTCUSDT", max_events=10)

        for event in [
            TradeEvent(1000, "BTCUSDT", 100, 5, True),
            TradeEvent(2000, "BTCUSDT", 110, 20, False),
            TradeEvent(3000, "BTCUSDT", 120, 3, True),
            TradeEvent(4000, "BTCUSDT", 130, 5, True),
            TradeEvent(5000, "BTCUSDT", 140, 30, False),
            TradeEvent(6000, "BTCUSDT", 150, 5, True),
            TradeEvent(7000, "BTCUSDT", 126, 12, False),
        ]:
            store.add_trade(event)

        view = store.view()

        self.assertEqual(view["summary"]["source"], "binance")
        self.assertEqual(view["summary"]["symbol"], "BTCUSDT")
        self.assertEqual(view["summary"]["trade_count"], 7)
        self.assertEqual(view["summary"]["last_price"], 126)
        self.assertTrue(any(level["type"] == "LVN" for level in view["profile_levels"]))
        self.assertGreater(len(view["delta_series"]), 0)

    def test_live_store_ignores_other_symbols(self):
        store = LiveOrderflowStore(symbol="BTCUSDT", max_events=10)

        store.add_trade(TradeEvent(1000, "ETHUSDT", 2000, 1, False))

        self.assertEqual(store.view()["summary"]["trade_count"], 0)

    def test_live_store_exposes_connection_status(self):
        store = LiveOrderflowStore(symbol="BTCUSDT", max_events=10)

        store.set_connection_status("error", "Install websockets")
        summary = store.view()["summary"]

        self.assertEqual(summary["connection_status"], "error")
        self.assertEqual(summary["connection_message"], "Install websockets")


if __name__ == "__main__":
    unittest.main()
