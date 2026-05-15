import unittest

from crypto_perp_tool.signals.setups import SetupCandidateEngine
from crypto_perp_tool.types import BiasResult, MarketSnapshot, MarketStateResult, ProfileLevel, ProfileLevelType, SignalSide


def _snapshot(price=102.0, delta=30.0):
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
        delta_15s=delta / 2,
        delta_30s=delta,
        delta_60s=delta,
        volume_30s=100.0,
        profile_levels=(
            ProfileLevel(ProfileLevelType.VAL, 99.0, 98.5, 99.5, 1.0, "execution_30m"),
            ProfileLevel(ProfileLevelType.POC, 100.0, 99.5, 100.5, 1.5, "execution_30m"),
            ProfileLevel(ProfileLevelType.VAH, 101.0, 100.5, 101.5, 1.0, "execution_30m"),
            ProfileLevel(ProfileLevelType.LVN, 102.0, 101.5, 102.5, 0.4, "execution_30m"),
        ),
    )


class SetupCandidateEngineTests(unittest.TestCase):
    def test_long_imbalance_generates_squeeze_candidate_with_legacy_setup(self):
        candidates = SetupCandidateEngine().generate(
            _snapshot(),
            MarketStateResult("imbalanced_up", "long"),
            BiasResult("long"),
        )

        self.assertEqual(candidates[0].setup_model, "squeeze_continuation")
        self.assertEqual(candidates[0].legacy_setup, "vah_breakout_lvn_pullback_aggression")
        self.assertEqual(candidates[0].side, SignalSide.LONG)

    def test_failed_auction_generates_reversal_candidate(self):
        candidates = SetupCandidateEngine().generate(
            _snapshot(price=100.4, delta=-20.0),
            MarketStateResult("failed_auction", "short"),
            BiasResult("short"),
        )

        self.assertEqual(candidates[0].setup_model, "failed_auction_reversal")
        self.assertEqual(candidates[0].legacy_setup, "cvd_divergence_failed_breakout")

    def test_lvn_acceptance_candidate_preserves_legacy_setup(self):
        candidates = SetupCandidateEngine().generate(
            _snapshot(price=102.7, delta=25.0),
            MarketStateResult("balanced", "neutral"),
            BiasResult("neutral"),
        )

        self.assertTrue(any(candidate.setup_model == "lvn_acceptance" for candidate in candidates))
        self.assertTrue(any(candidate.legacy_setup == "lvn_break_acceptance" for candidate in candidates))

    def test_neutral_bias_does_not_generate_squeeze(self):
        candidates = SetupCandidateEngine().generate(
            _snapshot(),
            MarketStateResult("compression", "neutral"),
            BiasResult("neutral"),
        )

        self.assertFalse(any(candidate.setup_model == "squeeze_continuation" for candidate in candidates))


if __name__ == "__main__":
    unittest.main()
