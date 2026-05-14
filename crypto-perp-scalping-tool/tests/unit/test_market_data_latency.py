import unittest

from crypto_perp_tool.market_data.latency import compute_exchange_lag_ms


class MarketDataLatencyTests(unittest.TestCase):
    def test_exchange_event_time_wins_over_received_time(self):
        lag = compute_exchange_lag_ms(event_time=1_000, exchange_event_time=1_240, received_at=125_000)

        self.assertEqual(lag, 240)

    def test_received_time_is_compatibility_fallback(self):
        lag = compute_exchange_lag_ms(event_time=1_000, received_at=1_375)

        self.assertEqual(lag, 375)

    def test_missing_timestamps_default_to_zero_lag(self):
        self.assertEqual(compute_exchange_lag_ms(event_time=1_000), 0)

    def test_negative_clock_skew_is_clamped_to_zero(self):
        lag = compute_exchange_lag_ms(event_time=1_000, exchange_event_time=900, received_at=800)

        self.assertEqual(lag, 0)


if __name__ == "__main__":
    unittest.main()
