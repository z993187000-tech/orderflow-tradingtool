import unittest
import time
from crypto_perp_tool.types import HistoricalWindows, MarketDataHealth


class HistoricalWindowsTests(unittest.TestCase):
    def test_default_windows_are_empty(self):
        w = HistoricalWindows()
        self.assertEqual(w.delta_30s, ())
        self.assertEqual(w.volume_30s, ())
        self.assertEqual(w.spread_5min, ())


class MarketDataHealthTests(unittest.TestCase):
    def test_high_latency_alone_is_not_websocket_stale(self):
        now = int(time.time() * 1000)
        health = MarketDataHealth(
            connection_status="connected",
            last_event_time=now,
            last_local_time=now + 2500,
            latency_ms=2500,
        )
        self.assertFalse(health.is_stale(max_data_lag_ms=2000))
        self.assertGreater(health.latency_ms, 2000)

    def test_is_not_stale_when_within_limits(self):
        now = int(time.time() * 1000)
        health = MarketDataHealth(
            connection_status="connected",
            last_event_time=now,
            last_local_time=now + 500,
            latency_ms=500,
        )
        self.assertFalse(health.is_stale())

    def test_is_stale_when_no_recent_event(self):
        old = int(time.time() * 1000) - 3000
        health = MarketDataHealth(
            connection_status="connected",
            last_event_time=old,
            last_local_time=old + 100,
            latency_ms=100,
        )
        self.assertTrue(health.is_stale(websocket_stale_ms=1500))

    def test_default_state_is_starting(self):
        health = MarketDataHealth()
        self.assertEqual(health.connection_status, "starting")


if __name__ == "__main__":
    unittest.main()
