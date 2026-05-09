import unittest

from crypto_perp_tool.profile import VolumeProfileEngine
from crypto_perp_tool.types import ProfileLevelType


class VolumeProfileEngineTests(unittest.TestCase):
    def test_profile_engine_identifies_poc_hvn_lvn_and_value_area(self):
        engine = VolumeProfileEngine(bin_size=10, value_area_ratio=0.70)

        for price, volume in [
            (100, 4),
            (110, 18),
            (120, 3),
            (130, 20),
            (140, 5),
        ]:
            engine.add_trade(price=price, quantity=volume)

        levels = engine.levels(window="rolling_4h")
        level_types = {level.type for level in levels}
        poc = next(level for level in levels if level.type == ProfileLevelType.POC)

        self.assertEqual(poc.price, 130)
        self.assertIn(ProfileLevelType.HVN, level_types)
        self.assertIn(ProfileLevelType.LVN, level_types)
        self.assertIn(ProfileLevelType.VAH, level_types)
        self.assertIn(ProfileLevelType.VAL, level_types)

    def test_value_area_levels_use_bin_boundaries_not_poc_center(self):
        engine = VolumeProfileEngine(bin_size=10, value_area_ratio=0.70)

        engine.add_trade(price=100, quantity=100)
        levels = engine.levels(window="rolling_4h")
        poc = next(level for level in levels if level.type == ProfileLevelType.POC)
        val = next(level for level in levels if level.type == ProfileLevelType.VAL)
        vah = next(level for level in levels if level.type == ProfileLevelType.VAH)

        self.assertEqual(poc.price, 100)
        self.assertEqual(val.price, 95)
        self.assertEqual(vah.price, 105)


if __name__ == "__main__":
    unittest.main()
