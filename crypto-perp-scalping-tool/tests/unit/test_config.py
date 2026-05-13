import unittest

from crypto_perp_tool.config import default_settings


class ConfigTests(unittest.TestCase):
    def test_default_settings_are_paper_first_and_symbol_limited(self):
        settings = default_settings()

        self.assertEqual(settings.mode, "paper")
        self.assertEqual(settings.exchange, "binance_futures")
        self.assertEqual(settings.symbols, ("BTCUSDT", "ETHUSDT"))
        self.assertEqual(settings.risk.risk_per_trade, 0.0025)
        self.assertEqual(settings.risk.max_leverage, 3)
        self.assertEqual(settings.profile.btc_bin_size, 5)


if __name__ == "__main__":
    unittest.main()
