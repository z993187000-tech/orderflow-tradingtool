import time
import unittest

from crypto_perp_tool.profile import VolumeProfileEngine
from crypto_perp_tool.types import ProfileLevelType


def _ts(minutes_ago: float = 0) -> int:
    return int((time.time() - minutes_ago * 60) * 1000)


class VolumeProfileEngineTests(unittest.TestCase):
    def setUp(self):
        self.now = int(time.time() * 1000)

    def test_identifies_poc_hvn_lvn_and_value_area_rolling(self):
        engine = VolumeProfileEngine(bin_size=10, value_area_ratio=0.70)
        for price, volume in [(100, 4), (110, 18), (120, 3), (130, 20), (140, 5)]:
            engine.add_trade(price=price, quantity=volume, timestamp=self.now)

        levels = engine.levels(window="rolling_4h")
        level_types = {level.type for level in levels}
        poc = next(level for level in levels if level.type == ProfileLevelType.POC)

        self.assertEqual(poc.price, 130)
        self.assertIn(ProfileLevelType.HVN, level_types)
        self.assertIn(ProfileLevelType.LVN, level_types)
        self.assertIn(ProfileLevelType.VAH, level_types)
        self.assertIn(ProfileLevelType.VAL, level_types)

    def test_value_area_uses_bin_boundaries(self):
        engine = VolumeProfileEngine(bin_size=10, value_area_ratio=0.70)
        engine.add_trade(price=100, quantity=100, timestamp=self.now)
        levels = engine.levels(window="rolling_4h")
        poc = next(level for level in levels if level.type == ProfileLevelType.POC)
        val = next(level for level in levels if level.type == ProfileLevelType.VAL)
        vah = next(level for level in levels if level.type == ProfileLevelType.VAH)
        self.assertEqual(poc.price, 100)
        self.assertEqual(val.price, 95)
        self.assertEqual(vah.price, 105)

    def test_backward_compatible_add_trade_without_timestamp(self):
        engine = VolumeProfileEngine(bin_size=10, value_area_ratio=0.70)
        engine.add_trade(price=100, quantity=10)
        levels = engine.levels(window="rolling_4h")
        self.assertEqual(len(levels), 3)  # POC, VAH, VAL (single bin)

    def test_session_window_filters_by_utc_day(self):
        engine = VolumeProfileEngine(bin_size=10, value_area_ratio=0.70)
        engine.add_trade(price=100, quantity=100, timestamp=self.now)
        levels = engine.levels(window="session")
        self.assertGreaterEqual(len(levels), 3)

    def test_rolling_window_excludes_old_trades(self):
        engine = VolumeProfileEngine(bin_size=10, value_area_ratio=0.70)
        old = self.now - 5 * 3600 * 1000  # 5 hours ago
        engine.add_trade(price=100, quantity=100, timestamp=old)
        engine.add_trade(price=200, quantity=100, timestamp=self.now)
        levels = engine.levels(window="rolling_4h")
        poc = next(level for level in levels if level.type == ProfileLevelType.POC)
        self.assertEqual(poc.price, 200)

    def test_session_high_low_tracks_extremes(self):
        engine = VolumeProfileEngine(bin_size=10, value_area_ratio=0.70)
        engine.add_trade(price=100, quantity=1, timestamp=self.now)
        engine.add_trade(price=200, quantity=1, timestamp=self.now)
        self.assertEqual(engine.session_high, 200)
        self.assertEqual(engine.session_low, 100)

    def test_evict_before_prunes_old_data(self):
        engine = VolumeProfileEngine(bin_size=10, value_area_ratio=0.70)
        old = self.now - 10 * 3600 * 1000
        engine.add_trade(price=100, quantity=100, timestamp=old)
        engine.add_trade(price=200, quantity=100, timestamp=self.now)
        engine._evict_before(self.now - 6 * 3600 * 1000)
        levels = engine.levels(window="rolling_4h")
        poc = next(level for level in levels if level.type == ProfileLevelType.POC)
        self.assertEqual(poc.price, 200)

    def test_evict_before_recalculates_session_extremes(self):
        engine = VolumeProfileEngine(bin_size=10, value_area_ratio=0.70)
        old = self.now - 10 * 3600 * 1000
        engine.add_trade(price=50, quantity=1, timestamp=old)
        engine.add_trade(price=300, quantity=1, timestamp=old)
        engine.add_trade(price=150, quantity=1, timestamp=self.now)
        self.assertEqual(engine.session_high, 300)
        engine._evict_before(self.now - 6 * 3600 * 1000)
        self.assertEqual(engine.session_high, 150)
        self.assertEqual(engine.session_low, 150)

    def test_evict_before_resets_extremes_when_empty(self):
        engine = VolumeProfileEngine(bin_size=10, value_area_ratio=0.70)
        old = self.now - 10 * 3600 * 1000
        engine.add_trade(price=100, quantity=1, timestamp=old)
        engine._evict_before(self.now - 6 * 3600 * 1000)
        self.assertIsNone(engine.session_high)
        self.assertIsNone(engine.session_low)


if __name__ == "__main__":
    unittest.main()
