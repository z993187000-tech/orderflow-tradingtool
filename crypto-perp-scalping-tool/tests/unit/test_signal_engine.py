import unittest
import time

from crypto_perp_tool.market_data import KlineEvent
from crypto_perp_tool.signals import SignalEngine
from crypto_perp_tool.types import HistoricalWindows, MarketDataHealth, MarketSnapshot, ProfileLevel, ProfileLevelType, SignalSide


def _level(level_type, price, window="execution_30m", strength=1.0):
    return ProfileLevel(level_type, price, price - 0.5, price + 0.5, strength, window)


def _snapshot(price=102.0, delta=40.0, volume=100.0, levels=None, event_time=120_000, local_time=120_000):
    return MarketSnapshot(
        exchange="binance_futures",
        symbol="BTCUSDT",
        event_time=event_time,
        local_time=local_time,
        last_price=price,
        bid_price=price - 0.1,
        ask_price=price + 0.1,
        spread_bps=2.0,
        vwap=100.0,
        atr_1m_14=2.0,
        atr_3m_14=2.0,
        delta_15s=delta / 2,
        delta_30s=delta,
        delta_60s=delta,
        volume_30s=volume,
        profile_levels=levels if levels is not None else (
            _level(ProfileLevelType.VAL, 99.0),
            _level(ProfileLevelType.POC, 100.0),
            _level(ProfileLevelType.VAH, 101.0),
            _level(ProfileLevelType.LVN, 102.0, strength=0.4),
            _level(ProfileLevelType.HVN, 108.0, "context_60m", 1.6),
        ),
    )


def _closed_kline(close=102.0, high=102.4, low=100.8):
    return KlineEvent(60_000, 119_999, "BTCUSDT", "1m", 101.0, high, low, close, 100.0, close * 100, 10, True)


class SignalEnginePipelineTests(unittest.TestCase):
    def test_rejects_candidate_without_1m_close_confirmation(self):
        engine = SignalEngine(min_reward_risk=1.2)

        signal = engine.evaluate(_snapshot(), windows=HistoricalWindows(delta_30s=(10.0,) * 20, volume_30s=(50.0,) * 20))

        self.assertIsNone(signal)
        self.assertEqual(engine.last_reject_reasons, ("candle_close_not_confirmed",))

    def test_confirmed_squeeze_signal_contains_pipeline_metadata(self):
        engine = SignalEngine(min_reward_risk=1.2)

        signal = engine.evaluate(
            _snapshot(),
            windows=HistoricalWindows(delta_30s=(10.0,) * 20, volume_30s=(50.0,) * 20),
            klines=(_closed_kline(close=102.0),),
        )

        self.assertIsNotNone(signal)
        self.assertEqual(signal.side, SignalSide.LONG)
        self.assertEqual(signal.setup_model, "squeeze_continuation")
        self.assertEqual(signal.setup, "vah_breakout_lvn_pullback_aggression")
        self.assertEqual(signal.market_state, "imbalanced_up")
        self.assertEqual(signal.bias, "long")
        self.assertEqual(signal.target_source, "context_60m_HVN")
        self.assertIsNotNone(signal.trace)
        self.assertIsNotNone(signal.trace.trade_plan)
        self.assertEqual(signal.trace.trade_plan.target_source, "context_60m_HVN")

    def test_auto_structure_target_with_low_reward_risk_falls_back_to_capped_r(self):
        engine = SignalEngine(min_reward_risk=1.2)
        levels = (
            _level(ProfileLevelType.VAL, 99.0),
            _level(ProfileLevelType.POC, 100.0),
            _level(ProfileLevelType.VAH, 101.0),
            ProfileLevel(ProfileLevelType.HVN, 102.6, 102.1, 103.1, 1.6, "context_60m"),
        )

        signal = engine.evaluate(
            _snapshot(levels=levels),
            windows=HistoricalWindows(delta_30s=(10.0,) * 20, volume_30s=(50.0,) * 20),
            klines=(_closed_kline(close=102.0),),
        )

        self.assertIsNotNone(signal)
        self.assertEqual(signal.target_source, "fallback_3.0R")
        self.assertEqual(engine.last_reject_reasons, ())

    def test_forbidden_conditions_still_run_before_pipeline(self):
        engine = SignalEngine(min_reward_risk=1.2)

        signal = engine.evaluate(_snapshot(), circuit_tripped=True, klines=(_closed_kline(close=102.0),))

        self.assertIsNone(signal)
        self.assertEqual(engine.last_reject_reasons, ("circuit_breaker_tripped",))

    def test_funding_blackout_rejects_before_pipeline(self):
        engine = SignalEngine(min_reward_risk=1.2)
        now = int(time.time() * 1000)
        health = MarketDataHealth("connected", now, now, 0)

        signal = engine.evaluate(
            _snapshot(event_time=now, local_time=now),
            health=health,
            next_funding_time=now + 1_000,
            klines=(_closed_kline(close=102.0),),
        )

        self.assertIsNone(signal)
        self.assertEqual(engine.last_reject_reasons, ("funding_blackout",))


if __name__ == "__main__":
    unittest.main()
