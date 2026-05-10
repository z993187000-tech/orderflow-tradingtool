import unittest

from crypto_perp_tool.risk.circuit import CircuitBreaker, CircuitBreakerReason


class CircuitBreakerTests(unittest.TestCase):
    def test_starts_in_normal_state(self):
        cb = CircuitBreaker()
        self.assertEqual(cb.state, "normal")
        self.assertIsNone(cb.reason)
        self.assertIsNone(cb.tripped_at)

    def test_trip_sets_state_and_reason(self):
        cb = CircuitBreaker()
        cb.trip(CircuitBreakerReason.DAILY_LOSS_LIMIT)
        self.assertEqual(cb.state, "tripped")
        self.assertEqual(cb.reason, CircuitBreakerReason.DAILY_LOSS_LIMIT)
        self.assertIsNotNone(cb.tripped_at)

    def test_can_resume_true_when_all_conditions_met(self):
        cb = CircuitBreaker()
        cb.trip(CircuitBreakerReason.WEBSOCKET_STALE)
        ok = cb.can_resume(
            account_ok=True,
            data_healthy=True,
            positions_reconciled=True,
            daily_loss_within_limit=True,
        )
        self.assertTrue(ok)

    def test_can_resume_false_when_positions_not_reconciled(self):
        cb = CircuitBreaker()
        cb.trip(CircuitBreakerReason.POSITION_MISMATCH)
        ok = cb.can_resume(
            account_ok=True,
            data_healthy=True,
            positions_reconciled=False,
            daily_loss_within_limit=True,
        )
        self.assertFalse(ok)

    def test_can_resume_false_when_daily_loss_exceeded(self):
        cb = CircuitBreaker()
        cb.trip(CircuitBreakerReason.DAILY_LOSS_LIMIT)
        ok = cb.can_resume(
            account_ok=True,
            data_healthy=True,
            positions_reconciled=True,
            daily_loss_within_limit=False,
        )
        self.assertFalse(ok)

    def test_resume_clears_trip_state(self):
        cb = CircuitBreaker()
        cb.trip(CircuitBreakerReason.WEBSOCKET_STALE)
        cb.resume(actor="telegram:12345")
        self.assertEqual(cb.state, "normal")
        self.assertIsNone(cb.reason)
        self.assertIsNone(cb.tripped_at)

    def test_resume_returns_resume_event(self):
        cb = CircuitBreaker()
        cb.trip(CircuitBreakerReason.WEBSOCKET_STALE)
        event = cb.resume(actor="telegram:12345")
        self.assertEqual(event["type"], "circuit_breaker_resumed")
        self.assertEqual(event["actor"], "telegram:12345")

    def test_cannot_resume_when_not_tripped(self):
        cb = CircuitBreaker()
        with self.assertRaises(RuntimeError):
            cb.resume(actor="telegram:12345")


if __name__ == "__main__":
    unittest.main()
