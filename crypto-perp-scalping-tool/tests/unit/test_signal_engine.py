import unittest

from crypto_perp_tool.signals import SignalEngine
from crypto_perp_tool.types import MarketSnapshot, ProfileLevel, ProfileLevelType, SignalSide


class SignalEngineTests(unittest.TestCase):
    def test_generates_long_signal_when_price_accepts_above_lvn(self):
        engine = SignalEngine(min_reward_risk=1.2)
        snapshot = MarketSnapshot(
            exchange="binance_futures",
            symbol="BTCUSDT",
            event_time=1000,
            local_time=1000,
            last_price=101.0,
            bid_price=100.9,
            ask_price=101.1,
            spread_bps=1.98,
            vwap=100.0,
            atr_1m_14=2.0,
            delta_15s=10.0,
            delta_30s=25.0,
            delta_60s=35.0,
            volume_30s=100.0,
            profile_levels=(
                ProfileLevel(ProfileLevelType.LVN, 100.0, 99.5, 100.5, 0.4, "rolling_4h"),
                ProfileLevel(ProfileLevelType.HVN, 105.0, 104.5, 105.5, 1.5, "rolling_4h"),
            ),
        )

        signal = engine.evaluate(snapshot)

        self.assertIsNotNone(signal)
        self.assertEqual(signal.side, SignalSide.LONG)
        self.assertEqual(signal.setup, "lvn_break_acceptance")
        self.assertGreaterEqual((signal.target_price - signal.entry_price) / (signal.entry_price - signal.stop_price), 1.2)

    def test_returns_none_when_data_is_stale(self):
        engine = SignalEngine(min_reward_risk=1.2)
        snapshot = MarketSnapshot(
            exchange="binance_futures",
            symbol="BTCUSDT",
            event_time=1000,
            local_time=4000,
            last_price=101.0,
            bid_price=100.9,
            ask_price=101.1,
            spread_bps=1.98,
            vwap=100.0,
            atr_1m_14=2.0,
            delta_15s=10.0,
            delta_30s=25.0,
            delta_60s=35.0,
            volume_30s=100.0,
            profile_levels=(ProfileLevel(ProfileLevelType.LVN, 100.0, 99.5, 100.5, 0.4, "rolling_4h"),),
        )

        self.assertIsNone(engine.evaluate(snapshot))


if __name__ == "__main__":
    unittest.main()
