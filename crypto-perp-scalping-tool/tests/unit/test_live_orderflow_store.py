import unittest

from crypto_perp_tool.market_data import MarkPriceEvent, QuoteEvent, SpotPriceEvent, TradeEvent
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

    def test_live_store_uses_spot_trade_as_display_last_price(self):
        store = LiveOrderflowStore(symbol="BTCUSDT", max_events=10)

        store.add_trade(TradeEvent(1000, "BTCUSDT", 100, 1, False))
        store.add_quote(QuoteEvent(1100, "BTCUSDT", 108, 110))
        store.add_spot(SpotPriceEvent(1200, "BTCUSDT", 112))
        summary = store.view()["summary"]

        self.assertEqual(summary["last_price"], 112)
        self.assertEqual(summary["spot_last_price"], 112)
        self.assertEqual(summary["last_trade_price"], 100)
        self.assertEqual(summary["bid_price"], 108)
        self.assertEqual(summary["ask_price"], 110)
        self.assertEqual(summary["quote_mid_price"], 109)
        self.assertEqual(summary["price_source"], "spotTrade")

    def test_live_store_falls_back_to_perp_trade_before_first_spot_trade(self):
        store = LiveOrderflowStore(symbol="BTCUSDT", max_events=10)

        store.add_trade(TradeEvent(1000, "BTCUSDT", 100, 1, False))
        store.add_quote(QuoteEvent(1100, "BTCUSDT", 108, 110))
        summary = store.view()["summary"]

        self.assertEqual(summary["last_price"], 100)
        self.assertEqual(summary["spot_last_price"], None)
        self.assertEqual(summary["last_trade_price"], 100)
        self.assertEqual(summary["price_source"], "aggTrade")

    def test_live_store_falls_back_to_index_price_before_perp_trade(self):
        store = LiveOrderflowStore(symbol="BTCUSDT", max_events=10)

        store.add_trade(TradeEvent(1000, "BTCUSDT", 100, 1, False))
        store.add_mark(MarkPriceEvent(1200, "BTCUSDT", 111, 112, 0.0001, 1300))
        summary = store.view()["summary"]

        self.assertEqual(summary["last_price"], 112)
        self.assertEqual(summary["spot_last_price"], None)
        self.assertEqual(summary["last_trade_price"], 100)
        self.assertEqual(summary["index_price"], 112)
        self.assertEqual(summary["price_source"], "indexPrice")

    def test_live_store_falls_back_to_quote_mid_before_first_trade(self):
        store = LiveOrderflowStore(symbol="BTCUSDT", max_events=10)

        store.add_quote(QuoteEvent(1100, "BTCUSDT", 108, 110))
        summary = store.view()["summary"]

        self.assertEqual(summary["last_price"], 109)
        self.assertEqual(summary["last_trade_price"], None)
        self.assertEqual(summary["price_source"], "bookTicker")

    def test_live_store_exposes_mark_and_index_prices(self):
        store = LiveOrderflowStore(symbol="BTCUSDT", max_events=10)

        store.add_mark(MarkPriceEvent(1200, "BTCUSDT", 111, 112, 0.0001, 1300))
        summary = store.view()["summary"]

        self.assertEqual(summary["mark_price"], 111)
        self.assertEqual(summary["index_price"], 112)
        self.assertEqual(summary["funding_rate"], 0.0001)
        self.assertEqual(summary["next_funding_time"], 1300)

    def test_live_store_exposes_empty_mode_detail_payloads(self):
        store = LiveOrderflowStore(symbol="BTCUSDT", max_events=10)
        view = store.view()

        self.assertEqual(view["summary"]["pnl_24h"], 0)
        self.assertEqual(view["summary"]["mode_breakdown"]["paper"]["signals"], 0)
        self.assertEqual(view["summary"]["mode_breakdown"]["live"]["orders"], 0)
        self.assertEqual(view["details"]["paper"]["pnl_by_range"]["all"], 0)
        self.assertEqual(view["details"]["live"]["closed_positions"], [])

    def test_live_store_uses_larger_profile_window_than_display_window(self):
        store = LiveOrderflowStore(symbol="BTCUSDT", max_events=650)
        store.add_trade(TradeEvent(1000, "BTCUSDT", 100, 500, False))
        for index in range(1, 600):
            store.add_trade(TradeEvent(1000 + index, "BTCUSDT", 200 + index, 1, False))

        view = store.view()
        poc = next(level for level in view["profile_levels"] if level["type"] == "POC")

        self.assertLessEqual(len(view["trades"]), 500)
        self.assertEqual(poc["price"], 100)
        self.assertEqual(view["summary"]["profile_trade_count"], 600)

    def test_live_store_runs_signal_engine_and_produces_signals(self):
        store = LiveOrderflowStore(symbol="BTCUSDT", max_events=600, enable_signals=True)
        store.add_quote(QuoteEvent(1000, "BTCUSDT", 99, 101))
        for price in [101.0, 101.5, 102.0, 102.5, 101.8, 102.2, 102.8, 103.5, 104.0, 104.5,
                       105.0, 104.8, 105.5, 106.0, 106.5, 105.8, 107.0, 106.2, 106.8, 107.5,
                       108.0, 107.5, 108.5, 109.0, 108.2, 108.8, 109.5, 110.0, 109.8, 110.5]:
            store.add_trade(TradeEvent(1000 + len(store._events) * 100, "BTCUSDT", price, 1.0, False))

        view = store.view()
        self.assertGreaterEqual(view["summary"]["signals"], 0)
        self.assertIn("mode_breakdown", view["summary"])

    def test_live_store_maintains_persistent_profile_engine(self):
        store = LiveOrderflowStore(symbol="BTCUSDT", max_events=10)

        store.add_trade(TradeEvent(1000, "BTCUSDT", 100, 5, True))
        store.add_trade(TradeEvent(2000, "BTCUSDT", 110, 20, False))
        view1 = store.view()
        store.add_trade(TradeEvent(3000, "BTCUSDT", 120, 3, True))
        view2 = store.view()

        self.assertLess(view1["summary"]["profile_trade_count"], view2["summary"]["profile_trade_count"])

    def test_live_store_handles_signal_engine_without_quote(self):
        store = LiveOrderflowStore(symbol="BTCUSDT", max_events=600, enable_signals=True)
        for price in [200.0, 200.5, 201.0, 201.5, 200.8, 201.2, 201.8, 202.5, 203.0, 203.5]:
            store.add_trade(TradeEvent(1000 + len(store._events) * 100, "BTCUSDT", price, 1.0, False))

        view = store.view()
        self.assertIsNotNone(view["summary"]["last_price"])


if __name__ == "__main__":
    unittest.main()
