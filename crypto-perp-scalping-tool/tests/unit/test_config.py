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
        self.assertEqual(settings.profile.value_area_ratio, 0.70)
        self.assertEqual(settings.profile.btc_bin_size, 20)
        self.assertEqual(settings.profile.eth_bin_size, 5)
        self.assertEqual(settings.profile.execution_window_minutes, 30)
        self.assertEqual(settings.profile.micro_window_minutes, 15)
        self.assertEqual(settings.profile.context_window_minutes, 60)
        self.assertEqual(settings.profile.min_execution_profile_trades, 50)
        self.assertEqual(settings.profile.min_micro_profile_trades, 25)
        self.assertEqual(settings.profile.min_profile_bins, 3)
        self.assertTrue(hasattr(settings.execution, "kline_stop_shift_consecutive_bars"))
        self.assertTrue(hasattr(settings.execution, "kline_stop_shift_reference_bars"))
        self.assertEqual(settings.execution.kline_stop_shift_consecutive_bars, 3)
        self.assertEqual(settings.execution.kline_stop_shift_reference_bars, 2)


if __name__ == "__main__":
    unittest.main()
