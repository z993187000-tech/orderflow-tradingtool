import unittest

from crypto_perp_tool.signals.trade_plan import TradePlanBuilder
from crypto_perp_tool.types import ConfirmationResult, MarketSnapshot, ProfileLevel, ProfileLevelType, SetupCandidate, SignalSide


def _snapshot(price=102.0, levels=None):
    return MarketSnapshot(
        exchange="binance_futures",
        symbol="BTCUSDT",
        event_time=1,
        local_time=1,
        last_price=price,
        bid_price=price - 0.1,
        ask_price=price + 0.1,
        spread_bps=2.0,
        vwap=100.0,
        atr_1m_14=2.0,
        delta_15s=20.0,
        delta_30s=40.0,
        delta_60s=40.0,
        volume_30s=100.0,
        profile_levels=levels if levels is not None else (
            ProfileLevel(ProfileLevelType.HVN, 108.0, 107.5, 108.5, 1.5, "context_60m"),
            ProfileLevel(ProfileLevelType.POC, 106.0, 105.5, 106.5, 1.7, "execution_30m"),
        ),
    )


def _candidate(side=SignalSide.LONG, stop=100.0, target=None):
    return SetupCandidate(
        setup_model="squeeze_continuation",
        legacy_setup="vah_breakout_lvn_pullback_aggression",
        side=side,
        trigger_price=101.0,
        location="above_value",
        structure_stop=stop,
        structure_target=target,
    )


class TradePlanBuilderTests(unittest.TestCase):
    def test_uses_context_structure_target_before_fallback_r(self):
        signal = TradePlanBuilder().build(
            _candidate(),
            ConfirmationResult(True, confirmed_close=102.0, displacement=1.0),
            _snapshot(),
        )

        self.assertIsNotNone(signal)
        self.assertEqual(signal.target_source, "context_60m_HVN")
        self.assertEqual(signal.target_price, 107.5)
        self.assertEqual(signal.setup_model, "squeeze_continuation")

    def test_rejects_when_structure_target_reward_risk_is_too_low(self):
        levels = (ProfileLevel(ProfileLevelType.HVN, 103.0, 102.1, 103.5, 1.5, "context_60m"),)
        builder = TradePlanBuilder()
        signal = builder.build(
            _candidate(stop=100.0, target=102.1),
            ConfirmationResult(True, confirmed_close=102.0, displacement=1.0),
            _snapshot(levels=levels),
        )

        self.assertIsNone(signal)
        self.assertEqual(builder.last_reject_reason, "structure_reward_risk_too_low")

    def test_fallback_reward_risk_is_capped(self):
        signal = TradePlanBuilder(max_reward_risk=4.0).build(
            _candidate(stop=100.0),
            ConfirmationResult(True, confirmed_close=102.0, displacement=1.0),
            _snapshot(levels=()),
        )

        self.assertIsNotNone(signal)
        self.assertEqual(signal.target_source, "fallback_3.0R")
        self.assertLessEqual(signal.target_r_multiple, 4.0)

    def test_short_stop_and_target_are_on_protective_sides(self):
        levels = (ProfileLevel(ProfileLevelType.HVN, 96.0, 95.5, 96.5, 1.5, "context_60m"),)
        signal = TradePlanBuilder().build(
            _candidate(side=SignalSide.SHORT, stop=99.0),
            ConfirmationResult(True, confirmed_close=98.0, displacement=1.0),
            _snapshot(price=98.0, levels=levels),
        )

        self.assertIsNotNone(signal)
        self.assertGreater(signal.stop_price, signal.entry_price)
        self.assertLess(signal.target_price, signal.entry_price)


if __name__ == "__main__":
    unittest.main()
