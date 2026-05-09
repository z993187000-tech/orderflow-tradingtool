import os
import unittest

from crypto_perp_tool.config import load_settings


class ConfigLoaderTests(unittest.TestCase):
    def test_live_mode_falls_back_to_paper_without_confirmation(self):
        previous = os.environ.pop("LIVE_TRADING_CONFIRMATION", None)
        try:
            settings = load_settings({"mode": "live"})
        finally:
            if previous is not None:
                os.environ["LIVE_TRADING_CONFIRMATION"] = previous

        self.assertEqual(settings.mode, "paper")
        self.assertIn("live_guard_missing_confirmation", settings.safety_warnings)

    def test_live_mode_requires_environment_confirmation(self):
        os.environ["LIVE_TRADING_CONFIRMATION"] = "I_UNDERSTAND_LIVE_RISK"
        try:
            settings = load_settings({"mode": "live"})
        finally:
            os.environ.pop("LIVE_TRADING_CONFIRMATION", None)

        self.assertEqual(settings.mode, "live")
        self.assertEqual(settings.safety_warnings, ())


if __name__ == "__main__":
    unittest.main()
