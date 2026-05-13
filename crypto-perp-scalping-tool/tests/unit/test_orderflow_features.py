import unittest

from crypto_perp_tool.market_data import TradeEvent
from crypto_perp_tool.market_data.features import AggressionBubbleDetector, AtrTracker


class OrderflowFeatureTests(unittest.TestCase):
    def test_aggression_bubble_detector_marks_buy_sell_and_size_tier(self):
        detector = AggressionBubbleDetector(large_threshold=20, block_threshold=50)

        self.assertIsNone(detector.detect(TradeEvent(1_000, "BTCUSDT", 100, 15, False)))

        buy = detector.detect(TradeEvent(2_000, "BTCUSDT", 101, 25, False))
        sell = detector.detect(TradeEvent(3_000, "BTCUSDT", 99, 55, True))

        self.assertIsNotNone(buy)
        self.assertEqual(buy.side, "buy")
        self.assertEqual(buy.tier, "large")
        self.assertEqual(buy.quantity, 25)
        self.assertEqual(buy.label, "BIG BUY 25.00")

        self.assertIsNotNone(sell)
        self.assertEqual(sell.side, "sell")
        self.assertEqual(sell.tier, "block")
        self.assertEqual(sell.label, "BLOCK SELL 55.00")

    def test_atr_tracker_uses_completed_one_minute_bars(self):
        tracker = AtrTracker(bar_ms=60_000, period=3)

        for event in [
            TradeEvent(1_000, "BTCUSDT", 100, 1, False),
            TradeEvent(20_000, "BTCUSDT", 105, 1, False),
            TradeEvent(59_000, "BTCUSDT", 99, 1, True),
            TradeEvent(60_000, "BTCUSDT", 102, 1, False),
            TradeEvent(90_000, "BTCUSDT", 108, 1, False),
            TradeEvent(119_000, "BTCUSDT", 101, 1, True),
            TradeEvent(120_000, "BTCUSDT", 107, 1, False),
            TradeEvent(150_000, "BTCUSDT", 110, 1, False),
            TradeEvent(179_000, "BTCUSDT", 106, 1, True),
        ]:
            tracker.update(event)

        atr = tracker.update(TradeEvent(180_000, "BTCUSDT", 109, 1, False))

        self.assertAlmostEqual(atr, 8.0)
        self.assertAlmostEqual(tracker.latest_atr, 8.0)


if __name__ == "__main__":
    unittest.main()
