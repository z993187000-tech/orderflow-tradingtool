import unittest

from crypto_perp_tool.market_data import KlineEvent
from crypto_perp_tool.signals.confirmation import ConfirmationGate
from crypto_perp_tool.types import HistoricalWindows, MarketSnapshot, SetupCandidate, SignalSide


def _snapshot(price=102.0, delta=30.0, volume=150.0, atr=2.0):
    return MarketSnapshot(
        exchange="binance_futures",
        symbol="BTCUSDT",
        event_time=120_000,
        local_time=120_000,
        last_price=price,
        bid_price=price - 0.1,
        ask_price=price + 0.1,
        spread_bps=2.0,
        vwap=100.0,
        atr_1m_14=atr,
        delta_15s=delta / 2,
        delta_30s=delta,
        delta_60s=delta,
        volume_30s=volume,
        profile_levels=(),
    )


def _candidate(side=SignalSide.LONG, trigger=101.0):
    return SetupCandidate(
        setup_model="squeeze_continuation",
        legacy_setup="vah_breakout_lvn_pullback_aggression",
        side=side,
        trigger_price=trigger,
        location="above_value",
        trigger_time=60_000,
    )


def _kline(close=101.5, high=102.0, low=100.8):
    return KlineEvent(60_000, 119_999, "BTCUSDT", "1m", 101.0, high, low, close, 100.0, close * 100, 10, True)


class ConfirmationGateTests(unittest.TestCase):
    def test_rejects_without_closed_candle(self):
        result = ConfirmationGate().confirm(_candidate(), _snapshot(), klines=(), windows=HistoricalWindows())

        self.assertFalse(result.confirmed)
        self.assertEqual(result.reject_reason, "candle_close_not_confirmed")

    def test_rejects_when_delta_does_not_confirm(self):
        result = ConfirmationGate().confirm(
            _candidate(),
            _snapshot(delta=5.0),
            klines=(_kline(close=102.0),),
            windows=HistoricalWindows(delta_30s=tuple([10.0] * 20), volume_30s=tuple([50.0] * 20)),
        )

        self.assertFalse(result.confirmed)
        self.assertEqual(result.reject_reason, "delta_not_confirmed")

    def test_rejects_when_price_reclaims_trigger(self):
        result = ConfirmationGate().confirm(
            _candidate(),
            _snapshot(price=100.8, delta=30.0),
            klines=(_kline(close=102.0),),
            windows=HistoricalWindows(delta_30s=tuple([10.0] * 20), volume_30s=tuple([50.0] * 20)),
        )

        self.assertFalse(result.confirmed)
        self.assertEqual(result.reject_reason, "trigger_reclaimed")

    def test_confirms_when_close_delta_volume_and_displacement_align(self):
        result = ConfirmationGate().confirm(
            _candidate(),
            _snapshot(price=102.0, delta=30.0, volume=100.0),
            klines=(_kline(close=102.0),),
            windows=HistoricalWindows(delta_30s=tuple([10.0] * 20), volume_30s=tuple([50.0] * 20)),
        )

        self.assertTrue(result.confirmed)
        self.assertGreaterEqual(result.displacement, 0.3)


if __name__ == "__main__":
    unittest.main()
