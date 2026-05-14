import unittest

from crypto_perp_tool.market_data import TimeWindowBuffer, TradeEvent


class TimeWindowBufferTests(unittest.TestCase):
    def test_items_since_uses_event_time_not_count(self):
        buffer = TimeWindowBuffer[TradeEvent](max_window_ms=60_000)
        old = TradeEvent(1_000, "BTCUSDT", 100, 10, False)
        recent = TradeEvent(121_000, "BTCUSDT", 101, 2, True)

        buffer.append(old.timestamp, old)
        buffer.append(recent.timestamp, recent)

        self.assertEqual(buffer.items_since(121_000, 60_000), [recent])
        self.assertEqual(buffer.count_since(121_000, 60_000), 1)

    def test_append_evicts_outside_max_window(self):
        buffer = TimeWindowBuffer[str](max_window_ms=30_000)

        buffer.append(1_000, "old")
        buffer.append(40_000, "recent")

        self.assertEqual(buffer.items(), ["recent"])
        self.assertEqual(buffer.latest_timestamp, 40_000)

    def test_append_returns_items_evicted_from_rolling_window(self):
        buffer = TimeWindowBuffer[str](max_window_ms=30_000)

        self.assertEqual(buffer.append(1_000, "old"), [])
        self.assertEqual(buffer.append(40_000, "recent"), ["old"])
        self.assertEqual(buffer.items(), ["recent"])

    def test_sum_since_uses_selected_values(self):
        buffer = TimeWindowBuffer[TradeEvent](max_window_ms=120_000)
        buffer.append(1_000, TradeEvent(1_000, "BTCUSDT", 100, 5, False))
        buffer.append(41_000, TradeEvent(41_000, "BTCUSDT", 101, 3, True))

        self.assertEqual(buffer.sum_since(41_000, 30_000, lambda event: event.delta), -3)
        self.assertEqual(buffer.sum_since(41_000, 60_000, lambda event: event.delta), 2)


if __name__ == "__main__":
    unittest.main()
