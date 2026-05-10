import time
import unittest

from crypto_perp_tool.market_data.health import compute_health


class MarketDataHealthTests(unittest.TestCase):
    def test_health_from_fresh_events_is_not_stale(self):
        now = int(time.time() * 1000)
        health = compute_health(
            connection_status="connected",
            last_event_time=now,
            last_local_time=now + 100,
            symbol="BTCUSDT",
        )
        self.assertEqual(health.connection_status, "connected")
        self.assertEqual(health.latency_ms, 100)
        self.assertFalse(health.is_stale())

    def test_health_tracks_reconnect_count(self):
        now = int(time.time() * 1000)
        health = compute_health(
            connection_status="connected",
            last_event_time=now,
            last_local_time=now + 50,
            reconnect_count=3,
        )
        self.assertEqual(health.reconnect_count, 3)

    def test_health_detects_high_latency(self):
        now = int(time.time() * 1000)
        health = compute_health(
            connection_status="connected",
            last_event_time=now - 100,
            last_local_time=now + 2000,
        )
        self.assertTrue(health.is_stale(max_data_lag_ms=1500))

    def test_health_detects_stale_connection(self):
        now = int(time.time() * 1000)
        health = compute_health(
            connection_status="connected",
            last_event_time=now - 3000,
            last_local_time=now - 2900,
        )
        self.assertTrue(health.is_stale(websocket_stale_ms=1500))


if __name__ == "__main__":
    unittest.main()
