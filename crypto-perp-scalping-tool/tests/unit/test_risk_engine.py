import unittest

from crypto_perp_tool.config import default_settings
from crypto_perp_tool.risk import AccountState, RiskEngine
from crypto_perp_tool.types import SignalSide, TradeSignal


class RiskEngineTests(unittest.TestCase):
    def test_risk_engine_sizes_position_from_stop_distance(self):
        engine = RiskEngine(default_settings().risk)
        signal = TradeSignal(
            id="sig-1",
            symbol="BTCUSDT",
            side=SignalSide.LONG,
            setup="lvn_break_acceptance",
            entry_price=100.0,
            stop_price=99.0,
            target_price=102.0,
            confidence=0.7,
            reasons=("accepted above LVN",),
            invalidation_rules=("back below LVN",),
            created_at=1,
        )

        decision = engine.evaluate(
            signal,
            AccountState(equity=10_000),
        )

        self.assertTrue(decision.allowed)
        self.assertEqual(decision.quantity, 25.0)
        self.assertEqual(decision.reject_reasons, ())


if __name__ == "__main__":
    unittest.main()
