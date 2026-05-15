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

EXECUTION_WINDOW = "execution_30m"
MICRO_WINDOW = "micro_15m"
CONTEXT_WINDOW = "context_60m"


def _snapshot(last_price=101.0, delta_30s=25.0, volume_30s=100.0, spread_bps=1.98,
              profile_levels=None, event_time=None, local_time=None, symbol="BTCUSDT",
              cumulative_delta=0.0, aggression_bubble_side=None,
              aggression_bubble_quantity=0.0, aggression_bubble_price=None,
              aggression_bubble_tier=None, atr_1m_14=2.0, atr_3m_14=2.0,
              exchange_event_time=None, delta_15s=None, include_default_micro=True,
              session="unknown"):
    now = int(time.time() * 1000)
    if profile_levels is None:
        profile_levels = (
            ProfileLevel(ProfileLevelType.LVN, 100.0, 99.5, 100.5, 0.4, EXECUTION_WINDOW),
            ProfileLevel(ProfileLevelType.HVN, 105.0, 104.5, 105.5, 1.5, EXECUTION_WINDOW),
        )
    if include_default_micro and not any(level.window == MICRO_WINDOW for level in profile_levels):
        profile_levels = (
            *profile_levels,
            ProfileLevel(ProfileLevelType.HVN, last_price, last_price - 0.5, last_price + 0.5, 1.2, MICRO_WINDOW),
        )
    if delta_15s is None:
        delta_15s = 10.0 if delta_30s >= 0 else -10.0
    return MarketSnapshot(
        exchange="binance_futures", symbol=symbol,
        event_time=event_time or now, local_time=local_time or now,
        last_price=last_price, bid_price=last_price - 0.1, ask_price=last_price + 0.1,
        spread_bps=spread_bps, vwap=last_price - 0.5, atr_1m_14=atr_1m_14,
        delta_15s=delta_15s, delta_30s=delta_30s, delta_60s=35.0, volume_30s=volume_30s,
        profile_levels=profile_levels,
        cumulative_delta=cumulative_delta,
        atr_3m_14=atr_3m_14,
        aggression_bubble_side=aggression_bubble_side,
        aggression_bubble_quantity=aggression_bubble_quantity,
        aggression_bubble_price=aggression_bubble_price,
        aggression_bubble_tier=aggression_bubble_tier,
        exchange_event_time=exchange_event_time,
        session=session,
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
            ProfileLevel(ProfileLevelType.LVN, 110.0, 109.5, 110.5, 0.4, EXECUTION_WINDOW),
            ProfileLevel(ProfileLevelType.HVN, 95.0, 94.5, 95.5, 1.5, EXECUTION_WINDOW),
        )
        signal = self.engine.evaluate(_snapshot(last_price=109.0, delta_30s=-25.0, profile_levels=levels))
        self.assertIsNotNone(signal)
        self.assertEqual(signal.side, SignalSide.SHORT)
        self.assertEqual(signal.setup, "lvn_breakdown_acceptance")

    def test_candidate_signal_requires_micro_profile_confirmation(self):
        snap = _snapshot(last_price=101.0, delta_30s=25.0, include_default_micro=False)

        self.assertIsNone(self.engine.evaluate(snap))

        self.assertEqual(self.engine.last_reject_reasons, ("micro_profile_insufficient",))

    def test_micro_profile_delta_must_align_with_entry_side(self):
        snap = _snapshot(last_price=101.0, delta_30s=25.0, delta_15s=-10.0)

        self.assertIsNone(self.engine.evaluate(snap))

        self.assertEqual(self.engine.last_reject_reasons, ("micro_delta_not_confirmed",))

    def test_context_profile_obstacle_can_reject_low_reward_risk(self):
        levels = (
            ProfileLevel(ProfileLevelType.LVN, 100.0, 99.5, 100.5, 0.4, EXECUTION_WINDOW),
            ProfileLevel(ProfileLevelType.HVN, 105.0, 104.5, 105.5, 1.5, EXECUTION_WINDOW),
            ProfileLevel(ProfileLevelType.HVN, 101.8, 101.8, 102.2, 1.7, CONTEXT_WINDOW),
        )
        snap = _snapshot(last_price=101.0, delta_30s=25.0, profile_levels=levels)

        self.assertIsNone(self.engine.evaluate(snap))

        self.assertEqual(self.engine.last_reject_reasons, ("context_reward_risk_too_low",))

    def test_dynamic_reward_risk_pushes_strong_trend_setup_to_upper_bound(self):
        engine = SignalEngine(min_reward_risk=1.2, reward_risk_min=3.0, reward_risk_max=10.0)
        now = int(time.time() * 1000)
        levels = (
            ProfileLevel(ProfileLevelType.VAH, 100.0, 99.5, 100.5, 1.4, EXECUTION_WINDOW),
            ProfileLevel(ProfileLevelType.LVN, 102.0, 101.5, 102.5, 0.4, EXECUTION_WINDOW),
            ProfileLevel(ProfileLevelType.HVN, 102.0, 101.5, 102.5, 1.2, MICRO_WINDOW),
        )
        engine._price_memory.append((now - 10_000, 101.2, 80.0))
        windows = HistoricalWindows(
            delta_30s=tuple([20.0] * 20),
            volume_30s=tuple([100.0] * 20),
            spread_5min=tuple([1.0] * 20),
            amplitude_1m=tuple([1.0] * 20),
        )

        signal = engine.evaluate(
            _snapshot(
                last_price=102.0,
                delta_15s=45.0,
                delta_30s=120.0,
                volume_30s=500.0,
                spread_bps=0.5,
                profile_levels=levels,
                aggression_bubble_side="buy",
                aggression_bubble_quantity=80.0,
                aggression_bubble_price=102.0,
                aggression_bubble_tier="block",
                atr_1m_14=1.0,
                atr_3m_14=1.0,
                event_time=now,
                local_time=now,
                session="london",
            ),
            windows=windows,
        )

        self.assertIsNotNone(signal)
        self.assertEqual(signal.setup, "vah_breakout_lvn_pullback_aggression")
        self.assertAlmostEqual(signal.target_r_multiple, 10.0)
        self.assertIn("target 10.0R", signal.reasons)

    def test_dynamic_reward_risk_keeps_weak_mean_reversion_at_lower_bound(self):
        engine = SignalEngine(min_reward_risk=1.2, reward_risk_min=3.0, reward_risk_max=10.0)
        windows = HistoricalWindows(
            delta_30s=tuple([20.0] * 20),
            volume_30s=tuple([50.0] * 20),
            spread_5min=tuple([1.0] * 20),
            amplitude_1m=tuple([1.0] * 20),
        )

        signal = engine.evaluate(
            _snapshot(
                last_price=101.0,
                delta_15s=5.0,
                delta_30s=24.5,
                volume_30s=75.0,
                spread_bps=1.9,
                atr_1m_14=2.0,
                atr_3m_14=2.0,
            ),
            windows=windows,
        )

        self.assertIsNotNone(signal)
        self.assertEqual(signal.setup, "lvn_break_acceptance")
        self.assertAlmostEqual(signal.target_r_multiple, 3.0)
        self.assertIn("target 3.0R", signal.reasons)

    def test_context_obstacle_rewrites_target_r_multiple_to_final_actual_reward_risk(self):
        engine = SignalEngine(min_reward_risk=1.2, reward_risk_min=3.0, reward_risk_max=10.0)
        levels = (
            ProfileLevel(ProfileLevelType.LVN, 100.0, 99.5, 100.5, 0.4, EXECUTION_WINDOW),
            ProfileLevel(ProfileLevelType.HVN, 105.0, 104.5, 105.5, 1.5, EXECUTION_WINDOW),
            ProfileLevel(ProfileLevelType.HVN, 103.2, 103.2, 103.6, 1.7, CONTEXT_WINDOW),
        )

        signal = engine.evaluate(
            _snapshot(last_price=101.0, delta_30s=25.0, profile_levels=levels)
        )

        self.assertIsNotNone(signal)
        actual_r = engine._reward_risk(signal.entry_price, signal.stop_price, signal.target_price, signal.side)
        self.assertAlmostEqual(signal.target_r_multiple, actual_r, places=4)
        self.assertLess(signal.target_r_multiple, 3.0)
        self.assertGreaterEqual(signal.target_r_multiple, engine.min_reward_risk)
        self.assertIn("context_60m obstacle target", signal.reasons)

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
            ProfileLevel(ProfileLevelType.VAL, val, val - 0.5, val + 0.5, 1.2, EXECUTION_WINDOW),
            ProfileLevel(ProfileLevelType.HVN, 105.0, 104.5, 105.5, 1.5, EXECUTION_WINDOW),
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
            ProfileLevel(ProfileLevelType.VAH, vah, vah - 0.5, vah + 0.5, 1.2, EXECUTION_WINDOW),
            ProfileLevel(ProfileLevelType.HVN, 95.0, 94.5, 95.5, 1.5, EXECUTION_WINDOW),
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
            ProfileLevel(ProfileLevelType.VAH, 110.0, 109.5, 110.5, 1.1, EXECUTION_WINDOW),
            ProfileLevel(ProfileLevelType.LVN, 112.0, 111.5, 112.5, 0.2, EXECUTION_WINDOW),
            ProfileLevel(ProfileLevelType.POC, 118.0, 117.5, 118.5, 1.8, EXECUTION_WINDOW),
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
            ProfileLevel(ProfileLevelType.VAL, 100.0, 99.5, 100.5, 1.1, EXECUTION_WINDOW),
            ProfileLevel(ProfileLevelType.LVN, 98.0, 97.5, 98.5, 0.2, EXECUTION_WINDOW),
            ProfileLevel(ProfileLevelType.POC, 92.0, 91.5, 92.5, 1.8, EXECUTION_WINDOW),
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

    def test_bearish_cvd_divergence_failed_breakout_targets_r_multiple(self):
        engine = SignalEngine(min_reward_risk=1.2)
        now = int(time.time() * 1000)
        levels = (
            ProfileLevel(ProfileLevelType.VAH, 110.0, 109.5, 110.5, 1.1, EXECUTION_WINDOW),
            ProfileLevel(ProfileLevelType.POC, 104.0, 103.5, 104.5, 1.8, EXECUTION_WINDOW),
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
        # Target is computed as entry - stop_distance * the signal's entry-time R.
        stop_distance = abs(109.8 - signal.stop_price)
        expected_target = 109.8 - stop_distance * signal.target_r_multiple
        self.assertAlmostEqual(signal.target_price, expected_target, places=2)
        self.assertIn("bearish CVD divergence", signal.reasons)

    def test_bullish_cvd_divergence_failed_breakdown_targets_r_multiple(self):
        engine = SignalEngine(min_reward_risk=1.2)
        now = int(time.time() * 1000)
        levels = (
            ProfileLevel(ProfileLevelType.VAL, 100.0, 99.5, 100.5, 1.1, EXECUTION_WINDOW),
            ProfileLevel(ProfileLevelType.POC, 106.0, 105.5, 106.5, 1.8, EXECUTION_WINDOW),
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
        # Target is computed as entry + stop_distance * the signal's entry-time R.
        stop_distance = abs(100.2 - signal.stop_price)
        expected_target = 100.2 + stop_distance * signal.target_r_multiple
        self.assertAlmostEqual(signal.target_price, expected_target, places=2)
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
