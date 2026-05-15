import unittest

from crypto_perp_tool.signals.bias import BiasEngine
from crypto_perp_tool.types import MarketSnapshot, MarketStateResult, ProfileLevel, ProfileLevelType


def _snapshot(price=102.0, vwap=100.0):
    return MarketSnapshot(
        exchange="binance_futures",
        symbol="BTCUSDT",
        event_time=1,
        local_time=1,
        last_price=price,
        bid_price=price - 0.1,
        ask_price=price + 0.1,
        spread_bps=2.0,
        vwap=vwap,
        atr_1m_14=1.0,
        delta_15s=1.0,
        delta_30s=1.0,
        delta_60s=1.0,
        volume_30s=10.0,
        profile_levels=(
            ProfileLevel(ProfileLevelType.VAL, 99.0, 98.5, 99.5, 1.0, "execution_30m"),
            ProfileLevel(ProfileLevelType.POC, 100.0, 99.5, 100.5, 1.5, "execution_30m"),
            ProfileLevel(ProfileLevelType.VAH, 101.0, 100.5, 101.5, 1.0, "execution_30m"),
        ),
    )


class BiasEngineTests(unittest.TestCase):
    def test_imbalanced_up_allows_long_bias(self):
        result = BiasEngine().evaluate(_snapshot(), MarketStateResult("imbalanced_up", "long"))

        self.assertEqual(result.bias, "long")

    def test_imbalanced_down_allows_short_bias(self):
        result = BiasEngine().evaluate(_snapshot(price=98.0, vwap=100.0), MarketStateResult("imbalanced_down", "short"))

        self.assertEqual(result.bias, "short")

    def test_balanced_state_is_neutral_near_poc(self):
        result = BiasEngine().evaluate(_snapshot(price=100.1, vwap=100.0), MarketStateResult("balanced", "neutral"))

        self.assertEqual(result.bias, "neutral")

    def test_absorption_uses_state_direction(self):
        result = BiasEngine().evaluate(_snapshot(price=100.2, vwap=100.0), MarketStateResult("absorption", "long"))

        self.assertEqual(result.bias, "long")


if __name__ == "__main__":
    unittest.main()
