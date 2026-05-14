import time
import unittest

from crypto_perp_tool.signals import SignalEngine
from crypto_perp_tool.types import (
    HistoricalWindows,
    MarketDataHealth,
    MarketSnapshot,
    ProfileLevel,
    ProfileLevelType,
    SignalSide,
)


def _snapshot(last_price=101.0, delta_30s=25.0, volume_30s=100.0, spread_bps=1.98,
              profile_levels=None, event_time=None, local_time=None, symbol="BTCUSDT",
              cumulative_delta=0.0, aggression_bubble_side=None,
              aggression_bubble_quantity=0.0, aggression_bubble_price=None,
              aggression_bubble_tier=None, atr_1m_14=2.0, atr_3m_14=2.0,
              exchange_event_time=None):
    now = int(time.time() * 1000)
    if profile_levels is None:
        profile_levels = (
            ProfileLevel(ProfileLevelType.LVN, 100.0, 99.5, 100.5, 0.4, "rolling_4h"),
            ProfileLevel(ProfileLevelType.HVN, 105.0, 104.5, 105.5, 1.5, "rolling_4h"),
        )
    return MarketSnapshot(
        exchange="binance_futures", symbol=symbol,
        event_time=event_time or now, local_time=local_time or now,
        last_price=last_price, bid_price=last_price - 0.1, ask_price=last_price + 0.1,
        spread_bps=spread_bps, vwap=last_price - 0.5, atr_1m_14=atr_1m_14,
        delta_15s=10.0, delta_30s=delta_30s, delta_60s=35.0, volume_30s=volume_30s,
        profile_levels=profile_levels,
        cumulative_delta=cumulative_delta,
        atr_3m_14=atr_3m_14,
        aggression_bubble_side=aggression_bubble_side,
        aggression_bubble_quantity=aggression_bubble_quantity,
        aggression_bubble_price=aggression_bubble_price,
        aggression_bubble_tier=aggression_bubble_tier,
        exchange_event_time=exchange_event_time,
    )


class SignalEngineTests(unittest.TestCase):
    def setUp(self):
        self.engine = SignalEngine(min_reward_risk=1.2)

    # --- Setup A: LVN acceptance ---

    def test_long_lvn_acceptance(self):
        signal = self.engine.evaluate(_snapshot(last_price=101.0, delta_30s=25.0))
        self.assertIsNotNone(signal)
        self.assertEqual(signal.side, SignalSide.LONG)
        self.assertEqual(signal.setup, "lvn_break_acceptance")

    def test_short_lvn_breakdown(self):
        levels = (
            ProfileLevel(ProfileLevelType.LVN, 110.0, 109.5, 110.5, 0.4, "rolling_4h"),
            ProfileLevel(ProfileLevelType.HVN, 95.0, 94.5, 95.5, 1.5, "rolling_4h"),
        )
        signal = self.engine.evaluate(_snapshot(last_price=109.0, delta_30s=-25.0, profile_levels=levels))
        self.assertIsNotNone(signal)
        self.assertEqual(signal.side, SignalSide.SHORT)
        self.assertEqual(signal.setup, "lvn_breakdown_acceptance")

    # --- Stale data ---

    def test_returns_none_when_data_is_stale(self):
        now = int(time.time() * 1000)
        snap = _snapshot(event_time=now, local_time=now, exchange_event_time=now + 3000)
        self.assertIsNone(self.engine.evaluate(snap))

    def test_ignores_wall_clock_skew_when_exchange_event_lag_is_fresh(self):
        now = int(time.time() * 1000)
        snap = _snapshot(event_time=now, local_time=now + 125_000, exchange_event_time=now + 250)

        signal = self.engine.evaluate(snap)

        self.assertIsNotNone(signal)
        self.assertNotIn("data_stale", self.engine.last_reject_reasons)

    # --- Spread forbidden ---

    def test_rejects_high_spread(self):
        windows = HistoricalWindows(spread_5min=tuple([1.0] * 20))
        snap = _snapshot(spread_bps=5.0)
        self.assertIsNone(self.engine.evaluate(snap, windows=windows))

    # --- Volume threshold ---

    def test_lvn_rejects_low_volume(self):
        windows = HistoricalWindows(volume_30s=tuple([200.0] * 20))
        snap = _snapshot(volume_30s=50.0)
        self.assertIsNone(self.engine.evaluate(snap, windows=windows))

    # --- Delta threshold ---

    def test_lvn_rejects_low_delta(self):
        windows = HistoricalWindows(delta_30s=tuple([100.0] * 20))
        snap = _snapshot(delta_30s=5.0)
        self.assertIsNone(self.engine.evaluate(snap, windows=windows))

    # --- Circuit breaker ---

    def test_rejects_when_circuit_tripped(self):
        self.assertIsNone(self.engine.evaluate(_snapshot(), circuit_tripped=True))

    # --- Existing position ---

    def test_rejects_when_has_position(self):
        self.assertIsNone(self.engine.evaluate(_snapshot(), has_position=True))

    # --- Setup B: Failed breakdown recovery ---

    def test_long_failed_breakdown_recovery(self):
        engine = SignalEngine(min_reward_risk=1.2)
        now = int(time.time() * 1000)
        val = 99.0
        levels = (
            ProfileLevel(ProfileLevelType.VAL, val, val - 0.5, val + 0.5, 1.2, "rolling_4h"),
            ProfileLevel(ProfileLevelType.HVN, 105.0, 104.5, 105.5, 1.5, "rolling_4h"),
        )
        # Feed price memory: dip below VAL, then recover
        for ms, price in [
            (now - 5000, 98.5),
            (now - 4000, 98.3),
            (now - 3000, 98.8),
            (now - 2000, 99.2),
            (now - 1000, 99.5),
        ]:
            engine._price_memory.append((ms, price))
        snap = _snapshot(last_price=99.5, delta_30s=15.0, profile_levels=levels)
        signal = engine.evaluate(snap)
        self.assertIsNotNone(signal)
        self.assertEqual(signal.setup, "hvn_val_failed_breakdown")

    def test_short_failed_breakout_recovery(self):
        engine = SignalEngine(min_reward_risk=1.2)
        now = int(time.time() * 1000)
        vah = 111.0
        levels = (
            ProfileLevel(ProfileLevelType.VAH, vah, vah - 0.5, vah + 0.5, 1.2, "rolling_4h"),
            ProfileLevel(ProfileLevelType.HVN, 95.0, 94.5, 95.5, 1.5, "rolling_4h"),
        )
        for ms, price in [
            (now - 5000, 111.5),
            (now - 4000, 112.0),
            (now - 3000, 111.2),
            (now - 2000, 110.4),
            (now - 1000, 110.2),
        ]:
            engine._price_memory.append((ms, price))
        snap = _snapshot(last_price=110.2, delta_30s=-15.0, profile_levels=levels)
        signal = engine.evaluate(snap)
        self.assertIsNotNone(signal)
        self.assertEqual(signal.setup, "hvn_vah_failed_breakout")

    # --- Trend squeeze: VAH/VAL breakout -> LVN pullback -> aggression bubble ---

    def test_long_vah_breakout_lvn_pullback_requires_buy_aggression_bubble(self):
        engine = SignalEngine(min_reward_risk=1.2)
        now = int(time.time() * 1000)
        levels = (
            ProfileLevel(ProfileLevelType.VAH, 110.0, 109.5, 110.5, 1.1, "rolling_4h"),
            ProfileLevel(ProfileLevelType.LVN, 112.0, 111.5, 112.5, 0.2, "rolling_4h"),
            ProfileLevel(ProfileLevelType.POC, 118.0, 117.5, 118.5, 1.8, "rolling_4h"),
        )
        for ms, price, cvd in [
            (now - 50_000, 109.8, 10.0),
            (now - 35_000, 111.0, 35.0),
            (now - 20_000, 113.0, 58.0),
        ]:
            engine._price_memory.append((ms, price, cvd))

        signal = engine.evaluate(_snapshot(
            last_price=112.0,
            delta_30s=60.0,
            profile_levels=levels,
            cumulative_delta=80.0,
            aggression_bubble_side="buy",
            aggression_bubble_quantity=25.0,
            aggression_bubble_price=111.8,
            aggression_bubble_tier="large",
            atr_1m_14=3.0,
            atr_3m_14=3.0,
        ))

        self.assertIsNotNone(signal)
        self.assertEqual(signal.side, SignalSide.LONG)
        self.assertEqual(signal.setup, "vah_breakout_lvn_pullback_aggression")
        self.assertLess(signal.stop_price, 111.8)
        self.assertIn("buy aggression bubble", signal.reasons)

    def test_short_val_breakdown_lvn_pullback_requires_sell_aggression_bubble(self):
        engine = SignalEngine(min_reward_risk=1.2)
        now = int(time.time() * 1000)
        levels = (
            ProfileLevel(ProfileLevelType.VAL, 100.0, 99.5, 100.5, 1.1, "rolling_4h"),
            ProfileLevel(ProfileLevelType.LVN, 98.0, 97.5, 98.5, 0.2, "rolling_4h"),
            ProfileLevel(ProfileLevelType.POC, 92.0, 91.5, 92.5, 1.8, "rolling_4h"),
        )
        for ms, price, cvd in [
            (now - 50_000, 100.2, -10.0),
            (now - 35_000, 99.0, -35.0),
            (now - 20_000, 97.0, -58.0),
        ]:
            engine._price_memory.append((ms, price, cvd))

        signal = engine.evaluate(_snapshot(
            last_price=98.0,
            delta_30s=-60.0,
            profile_levels=levels,
            cumulative_delta=-80.0,
            aggression_bubble_side="sell",
            aggression_bubble_quantity=50.0,
            aggression_bubble_price=98.2,
            aggression_bubble_tier="block",
            atr_1m_14=3.0,
            atr_3m_14=3.0,
        ))

        self.assertIsNotNone(signal)
        self.assertEqual(signal.side, SignalSide.SHORT)
        self.assertEqual(signal.setup, "val_breakdown_lvn_pullback_aggression")
        self.assertGreater(signal.stop_price, 98.2)
        self.assertIn("sell aggression bubble", signal.reasons)

    # --- CVD divergence failed auction ---

    def test_bearish_cvd_divergence_failed_breakout_targets_poc(self):
        engine = SignalEngine(min_reward_risk=1.2)
        now = int(time.time() * 1000)
        levels = (
            ProfileLevel(ProfileLevelType.VAH, 110.0, 109.5, 110.5, 1.1, "rolling_4h"),
            ProfileLevel(ProfileLevelType.POC, 104.0, 103.5, 104.5, 1.8, "rolling_4h"),
        )
        for ms, price, cvd in [
            (now - 55_000, 111.0, 100.0),
            (now - 35_000, 112.0, 80.0),
            (now - 10_000, 110.2, 70.0),
        ]:
            engine._price_memory.append((ms, price, cvd))

        signal = engine.evaluate(_snapshot(
            last_price=109.8,
            delta_30s=-25.0,
            profile_levels=levels,
            cumulative_delta=65.0,
            atr_1m_14=2.0,
            atr_3m_14=2.0,
        ))

        self.assertIsNotNone(signal)
        self.assertEqual(signal.side, SignalSide.SHORT)
        self.assertEqual(signal.setup, "cvd_divergence_failed_breakout")
        self.assertEqual(signal.target_price, 104.0)
        self.assertIn("bearish CVD divergence", signal.reasons)

    def test_bullish_cvd_divergence_failed_breakdown_targets_poc(self):
        engine = SignalEngine(min_reward_risk=1.2)
        now = int(time.time() * 1000)
        levels = (
            ProfileLevel(ProfileLevelType.VAL, 100.0, 99.5, 100.5, 1.1, "rolling_4h"),
            ProfileLevel(ProfileLevelType.POC, 106.0, 105.5, 106.5, 1.8, "rolling_4h"),
        )
        for ms, price, cvd in [
            (now - 55_000, 99.0, -100.0),
            (now - 35_000, 98.0, -80.0),
            (now - 10_000, 99.8, -70.0),
        ]:
            engine._price_memory.append((ms, price, cvd))

        signal = engine.evaluate(_snapshot(
            last_price=100.2,
            delta_30s=25.0,
            profile_levels=levels,
            cumulative_delta=-65.0,
            atr_1m_14=2.0,
            atr_3m_14=2.0,
        ))

        self.assertIsNotNone(signal)
        self.assertEqual(signal.side, SignalSide.LONG)
        self.assertEqual(signal.setup, "cvd_divergence_failed_breakdown")
        self.assertEqual(signal.target_price, 106.0)
        self.assertIn("bullish CVD divergence", signal.reasons)

    # --- Funding blackout ---

    def test_rejects_during_funding_blackout(self):
        now = int(time.time() * 1000)
        health = MarketDataHealth(
            connection_status="connected",
            last_event_time=now,
            last_local_time=now,
            latency_ms=100,
        )
        snap = _snapshot(event_time=now, local_time=now)
        # next_funding_time is within 2 min (120 seconds)
        self.assertIsNone(self.engine.evaluate(snap, health=health, next_funding_time=now + 30_000))


if __name__ == "__main__":
    unittest.main()
