import time
import unittest

from crypto_perp_tool.serialization import to_jsonable
from crypto_perp_tool.types import (
    BiasResult,
    ConfirmationResult,
    HistoricalWindows,
    MarketDataHealth,
    MarketSnapshot,
    MarketStateResult,
    SetupCandidate,
    SignalSide,
    SignalTrace,
    TradePlan,
)


class HistoricalWindowsTests(unittest.TestCase):
    def test_default_windows_are_empty(self):
        w = HistoricalWindows()
        self.assertEqual(w.delta_30s, ())
        self.assertEqual(w.volume_30s, ())
        self.assertEqual(w.spread_5min, ())


class MarketDataHealthTests(unittest.TestCase):
    def test_high_latency_alone_is_not_websocket_stale(self):
        now = int(time.time() * 1000)
        health = MarketDataHealth(
            connection_status="connected",
            last_event_time=now,
            last_local_time=now + 2500,
            latency_ms=2500,
        )
        self.assertFalse(health.is_stale(max_data_lag_ms=2000))
        self.assertGreater(health.latency_ms, 2000)

    def test_is_not_stale_when_within_limits(self):
        now = int(time.time() * 1000)
        health = MarketDataHealth(
            connection_status="connected",
            last_event_time=now,
            last_local_time=now + 500,
            latency_ms=500,
        )
        self.assertFalse(health.is_stale())

    def test_is_stale_when_no_recent_event(self):
        old = int(time.time() * 1000) - 3000
        health = MarketDataHealth(
            connection_status="connected",
            last_event_time=old,
            last_local_time=old + 100,
            latency_ms=100,
        )
        self.assertTrue(health.is_stale(websocket_stale_ms=1500))

    def test_default_state_is_starting(self):
        health = MarketDataHealth()
        self.assertEqual(health.connection_status, "starting")


class MarketSnapshotTests(unittest.TestCase):
    def test_exchange_lag_uses_exchange_event_time_when_present(self):
        snapshot = MarketSnapshot(
            exchange="binance",
            symbol="BTCUSDT",
            event_time=1_000,
            local_time=99_999,
            exchange_event_time=1_250,
            last_price=100,
            bid_price=99,
            ask_price=101,
            spread_bps=200,
            vwap=100,
            atr_1m_14=1,
            delta_15s=0,
            delta_30s=0,
            delta_60s=0,
            volume_30s=0,
            profile_levels=(),
        )

        self.assertEqual(snapshot.exchange_lag_ms, 250)

    def test_exchange_lag_falls_back_to_local_time_when_exchange_time_missing(self):
        snapshot = MarketSnapshot(
            exchange="binance",
            symbol="BTCUSDT",
            event_time=1_000,
            local_time=1_375,
            last_price=100,
            bid_price=99,
            ask_price=101,
            spread_bps=200,
            vwap=100,
            atr_1m_14=1,
            delta_15s=0,
            delta_30s=0,
            delta_60s=0,
            volume_30s=0,
            profile_levels=(),
        )

        self.assertEqual(snapshot.exchange_lag_ms, 375)


class StrategyPipelineTypeTests(unittest.TestCase):
    def test_strategy_pipeline_types_are_jsonable(self):
        candidate = SetupCandidate(
            setup_model="squeeze_continuation",
            legacy_setup="vah_breakout_lvn_pullback_aggression",
            side=SignalSide.LONG,
            trigger_price=101.0,
            location="above_value",
            reasons=("seller aggression failed",),
        )
        confirmation = ConfirmationResult(
            confirmed=True,
            reasons=("1m close confirmed",),
            confirmed_close=102.0,
            displacement=1.0,
        )
        plan = TradePlan(
            entry_price=102.0,
            stop_price=100.0,
            target_price=108.0,
            target_source="context_60m_HVN",
            reward_risk=3.0,
            management_profile="squeeze",
        )
        trace = SignalTrace(
            market_state=MarketStateResult(state="imbalanced_up", direction="long", reasons=("above VAH",)),
            bias=BiasResult(bias="long", reasons=("value accepted higher",)),
            location="above_value",
            trigger="breakout",
            confirmation=confirmation,
            trade_plan=plan,
            reject_reasons=(),
        )

        payload = to_jsonable({
            "candidate": candidate,
            "trace": trace,
        })

        self.assertEqual(payload["candidate"]["setup_model"], "squeeze_continuation")
        self.assertEqual(payload["candidate"]["side"], "long")
        self.assertEqual(payload["trace"]["market_state"]["state"], "imbalanced_up")
        self.assertEqual(payload["trace"]["trade_plan"]["target_source"], "context_60m_HVN")


if __name__ == "__main__":
    unittest.main()
