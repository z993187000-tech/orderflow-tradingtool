import unittest

from crypto_perp_tool.execution.paper_engine import PaperTradingEngine
from crypto_perp_tool.market_data import TradeEvent


def sample_trade_events() -> list[TradeEvent]:
    return [
        TradeEvent(1000, "BTCUSDT", 100, 5, True),
        TradeEvent(2000, "BTCUSDT", 110, 20, False),
        TradeEvent(3000, "BTCUSDT", 120, 3, True),
        TradeEvent(4000, "BTCUSDT", 130, 5, True),
        TradeEvent(5000, "BTCUSDT", 140, 30, False),
        TradeEvent(6000, "BTCUSDT", 150, 5, True),
        TradeEvent(7000, "BTCUSDT", 126, 12, False),
        TradeEvent(8000, "BTCUSDT", 141, 10, False),
    ]


class PaperTradingEngineTests(unittest.TestCase):
    def test_processes_trade_stream_into_signal_order_and_closed_position(self):
        engine = PaperTradingEngine(symbol="BTCUSDT", equity=10_000, signal_cooldown_ms=0)

        for event in sample_trade_events():
            engine.process_trade(event)

        summary = engine.summary()
        details = engine.details()
        paper = details["paper"]

        self.assertGreaterEqual(summary["signals"], 1)
        self.assertGreaterEqual(summary["orders"], 1)
        self.assertGreaterEqual(summary["closed_positions"], 1)
        self.assertGreater(summary["realized_pnl"], 0)
        self.assertEqual(summary["open_position"], None)
        self.assertEqual(len(paper["signals"]), summary["signals"])
        self.assertEqual(len(paper["orders"]), summary["orders"])
        self.assertEqual(len(paper["closed_positions"]), summary["closed_positions"])
        self.assertGreater(paper["pnl_by_range"]["all"], 0)

        marker_types = {marker["type"] for marker in engine.markers()}
        self.assertIn("signal", marker_types)
        self.assertIn("position_closed", marker_types)

    def test_does_not_open_a_new_order_while_position_is_open(self):
        engine = PaperTradingEngine(symbol="BTCUSDT", equity=10_000, signal_cooldown_ms=0)

        for event in sample_trade_events()[:-1]:
            engine.process_trade(event)
        first_summary = engine.summary()
        self.assertEqual(first_summary["orders"], 1)
        self.assertIsNotNone(first_summary["open_position"])

        for event in [
            TradeEvent(7100, "BTCUSDT", 127, 15, False),
            TradeEvent(7200, "BTCUSDT", 128, 15, False),
            TradeEvent(7300, "BTCUSDT", 129, 15, False),
        ]:
            engine.process_trade(event)

        self.assertEqual(engine.summary()["orders"], 1)

    def test_ignores_other_symbols(self):
        engine = PaperTradingEngine(symbol="BTCUSDT", equity=10_000)

        engine.process_trade(TradeEvent(1000, "ETHUSDT", 2000, 1, False))

        self.assertEqual(engine.summary()["profile_trade_count"], 0)
        self.assertEqual(engine.summary()["signals"], 0)


if __name__ == "__main__":
    unittest.main()
