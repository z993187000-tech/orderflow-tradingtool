import unittest

from crypto_perp_tool.market_data import KlineEvent
from crypto_perp_tool.signals.market_state import MarketStateEngine
from crypto_perp_tool.types import HistoricalWindows, MarketSnapshot, ProfileLevel, ProfileLevelType


def _level(level_type, price, window="execution_30m"):
    return ProfileLevel(level_type, price, price - 0.5, price + 0.5, 1.0, window)


def _snapshot(price=101.0, delta=20.0, volume=100.0, levels=None, vwap=100.0,
              bubble_side=None, bubble_price=None, atr=2.0):
    return MarketSnapshot(
        exchange="binance_futures",
        symbol="BTCUSDT",
        event_time=10_000,
        local_time=10_000,
        last_price=price,
        bid_price=price - 0.1,
        ask_price=price + 0.1,
        spread_bps=2.0,
        vwap=vwap,
        atr_1m_14=atr,
        delta_15s=delta / 2,
        delta_30s=delta,
        delta_60s=delta,
        volume_30s=volume,
        profile_levels=levels if levels is not None else (
            _level(ProfileLevelType.VAL, 99.0),
            _level(ProfileLevelType.POC, 100.0),
            _level(ProfileLevelType.VAH, 101.0),
        ),
        aggression_bubble_side=bubble_side,
        aggression_bubble_price=bubble_price,
        aggression_bubble_tier="large" if bubble_side else None,
    )


def _kline(open_, high, low, close, ts=0, volume=100.0):
    return KlineEvent(ts, ts + 59_999, "BTCUSDT", "1m", open_, high, low, close, volume, volume * close, 10, True)


class MarketStateEngineTests(unittest.TestCase):
    def test_detects_imbalanced_up_when_price_accepts_above_value(self):
        state = MarketStateEngine().evaluate(_snapshot(price=102.0, delta=30.0))

        self.assertEqual(state.state, "imbalanced_up")
        self.assertEqual(state.direction, "long")

    def test_detects_absorption_when_large_delta_has_little_displacement(self):
        windows = HistoricalWindows(delta_30s=tuple([10.0] * 20))
        state = MarketStateEngine().evaluate(
            _snapshot(price=100.1, delta=-35.0, bubble_side="sell", bubble_price=100.0),
            windows=windows,
        )

        self.assertEqual(state.state, "absorption")
        self.assertEqual(state.direction, "long")

    def test_detects_failed_auction_when_breakout_closes_back_inside_value(self):
        state = MarketStateEngine().evaluate(
            _snapshot(price=100.4, delta=-20.0),
            klines=(_kline(101.0, 102.2, 100.0, 100.4),),
        )

        self.assertEqual(state.state, "failed_auction")
        self.assertEqual(state.direction, "short")

    def test_failed_auction_uses_nearest_matching_level(self):
        levels = (
            _level(ProfileLevelType.VAH, 101.0),
            _level(ProfileLevelType.VAH, 110.0),
            _level(ProfileLevelType.VAL, 99.0),
            _level(ProfileLevelType.POC, 100.0),
        )
        state = MarketStateEngine().evaluate(
            _snapshot(price=109.4, delta=-20.0, levels=levels),
            klines=(_kline(110.0, 110.8, 109.0, 109.4),),
        )

        self.assertEqual(state.state, "failed_auction")
        self.assertEqual(state.direction, "short")

    def test_detects_compression_when_recent_ranges_contract_near_level(self):
        klines = (
            _kline(100, 105, 99, 101, 1),
            _kline(101, 106, 100, 102, 2),
            _kline(102, 103.0, 101.8, 102.4, 3),
            _kline(102.4, 103.1, 102.0, 102.8, 4),
            _kline(102.8, 103.2, 102.5, 102.9, 5),
            _kline(102.9, 103.3, 102.7, 103.0, 6),
            _kline(103.0, 103.4, 102.9, 103.1, 7),
        )
        state = MarketStateEngine().evaluate(_snapshot(price=101.2, delta=5.0), klines=klines)

        self.assertEqual(state.state, "compression")

    def test_returns_no_trade_without_profile_levels(self):
        state = MarketStateEngine().evaluate(_snapshot(levels=()))

        self.assertEqual(state.state, "no_trade")


if __name__ == "__main__":
    unittest.main()
