import unittest

from crypto_perp_tool.market_data.distribution import TradeSizeDistribution


class TradeSizeDistributionTests(unittest.TestCase):
    def test_cold_start_returns_true_initially(self):
        dist = TradeSizeDistribution()
        self.assertTrue(dist.is_cold_start())

    def test_cold_start_false_after_enough_data(self):
        dist = TradeSizeDistribution()
        for i in range(100):
            dist.add(1.0, i * 1000)
        self.assertFalse(dist.is_cold_start())

    def test_percentile_95_basic(self):
        dist = TradeSizeDistribution(half_life_ms=999_999_999)
        for _ in range(100):
            dist.add(1.0, 0)
        for _ in range(5):
            dist.add(100.0, 0)
        p95 = dist.percentile(0.95)
        self.assertGreater(p95, 1.0)

    def test_percentile_zero_total_returns_zero(self):
        dist = TradeSizeDistribution()
        self.assertEqual(dist.percentile(0.95), 0.0)

    def test_sub_min_edge_ignored(self):
        dist = TradeSizeDistribution(min_edge=0.01)
        dist.add(0.001, 0)
        self.assertTrue(dist.is_cold_start())
        self.assertEqual(dist._observation_count, 0)

    def test_overflow_goes_to_last_bin(self):
        dist = TradeSizeDistribution(half_life_ms=999_999_999, bin_count=10, min_edge=1.0)
        dist.add(99999.0, 0)
        p99 = dist.percentile(0.99)
        self.assertGreaterEqual(p99, 1.0)

    def test_decay_reduces_counts(self):
        dist = TradeSizeDistribution(half_life_ms=1000)
        for _ in range(200):
            dist.add(1.0, 0)
        self.assertFalse(dist.is_cold_start())
        dist.add(1.0, 2000)
        self.assertLess(dist._bins[0], 100)

    def test_observation_count_tracks_additions(self):
        dist = TradeSizeDistribution(half_life_ms=999_999_999)
        for _ in range(50):
            dist.add(1.0, 0)
        self.assertEqual(dist._observation_count, 50)


if __name__ == "__main__":
    unittest.main()
