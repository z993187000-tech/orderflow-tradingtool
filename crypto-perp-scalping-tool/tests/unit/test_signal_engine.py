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
              profile_levels=None, event_time=None, local_time=None, symbol="BTCUSDT"):
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
        spread_bps=spread_bps, vwap=last_price - 0.5, atr_1m_14=2.0,
        delta_15s=10.0, delta_30s=delta_30s, delta_60s=35.0, volume_30s=volume_30s,
        profile_levels=profile_levels,
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
        snap = _snapshot(event_time=now - 3000, local_time=now)
        self.assertIsNone(self.engine.evaluate(snap))

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
