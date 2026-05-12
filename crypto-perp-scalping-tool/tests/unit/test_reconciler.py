import unittest

from crypto_perp_tool.execution.reconciler import (
    PositionReconciler,
    ReconciledPosition,
    ReconciliationStatus,
)


class PositionReconcilerTests(unittest.TestCase):
    def setUp(self):
        self.reconciler = PositionReconciler(mode="paper")

    def test_initial_state_has_no_positions(self):
        self.assertFalse(self.reconciler.has_local_position("BTCUSDT"))
        self.assertIsNone(self.reconciler.get_local_position("BTCUSDT"))

    def test_set_and_get_local_position(self):
        self.reconciler.set_local_position("BTCUSDT", {
            "side": "long",
            "quantity": 0.01,
            "entry_price": 96000,
            "stop_price": 95500,
            "target_price": 97000,
        })

        self.assertTrue(self.reconciler.has_local_position("BTCUSDT"))
        pos = self.reconciler.get_local_position("BTCUSDT")
        self.assertIsNotNone(pos)
        self.assertEqual(pos.side, "long")
        self.assertEqual(pos.quantity, 0.01)
        self.assertEqual(pos.entry_price, 96000)

    def test_clear_position(self):
        self.reconciler.set_local_position("BTCUSDT", {
            "side": "long", "quantity": 0.01, "entry_price": 96000,
        })
        self.reconciler.set_local_position("BTCUSDT", None)

        self.assertFalse(self.reconciler.has_local_position("BTCUSDT"))

    def test_set_position_from_reconciled_object(self):
        pos = ReconciledPosition(
            symbol="ETHUSDT", side="short", quantity=0.5,
            entry_price=3200, stop_price=3250, target_price=3100,
        )
        self.reconciler.set_local_position("ETHUSDT", pos)
        retrieved = self.reconciler.get_local_position("ETHUSDT")
        self.assertEqual(retrieved.side, "short")
        self.assertEqual(retrieved.entry_price, 3200)

    def test_reconcile_ok_when_no_positions(self):
        result = self.reconciler.reconcile()
        self.assertEqual(result.status, ReconciliationStatus.OK)
        self.assertFalse(result.should_pause)

    def test_reconcile_detects_missing_exchange_position(self):
        self.reconciler.set_local_position("BTCUSDT", {
            "side": "long", "quantity": 0.01, "entry_price": 96000,
        })
        result = self.reconciler.reconcile(exchange_positions={})
        self.assertEqual(result.status, ReconciliationStatus.MISMATCH)
        self.assertTrue(result.should_pause)
        self.assertGreater(len(result.mismatches), 0)

    def test_reconcile_detects_exchange_only_position(self):
        result = self.reconciler.reconcile(
            exchange_positions={
                "BTCUSDT": {"quantity": 0.01, "entry_price": 96000, "side": "long"},
            }
        )
        self.assertEqual(result.status, ReconciliationStatus.EXCHANGE_ONLY)
        self.assertTrue(result.should_pause)

    def test_reconcile_detects_quantity_mismatch(self):
        self.reconciler.set_local_position("BTCUSDT", {
            "side": "long", "quantity": 0.01, "entry_price": 96000,
        })
        result = self.reconciler.reconcile(
            exchange_positions={
                "BTCUSDT": {"quantity": 0.02, "entry_price": 96000, "side": "long"},
            }
        )
        self.assertEqual(result.status, ReconciliationStatus.MISMATCH)

    def test_reconcile_detects_missing_stop_order(self):
        self.reconciler.set_local_position("BTCUSDT", {
            "side": "long", "quantity": 0.01, "entry_price": 96000,
        })
        result = self.reconciler.reconcile(
            exchange_positions={
                "BTCUSDT": {"quantity": 0.01, "entry_price": 96000, "side": "long"},
            },
            exchange_orders={
                "BTCUSDT": [],  # empty - no stop order
            },
        )
        self.assertEqual(result.status, ReconciliationStatus.MISSING_PROTECTION)
        self.assertTrue(result.should_pause)

    def test_reconcile_ok_when_all_match(self):
        self.reconciler.set_local_position("BTCUSDT", {
            "side": "long", "quantity": 0.01, "entry_price": 96000,
        })
        result = self.reconciler.reconcile(
            exchange_positions={
                "BTCUSDT": {"quantity": 0.01, "entry_price": 96000, "side": "long"},
            },
            exchange_orders={
                "BTCUSDT": [
                    {"type": "STOP_MARKET", "reduceOnly": True, "price": 95500},
                ],
            },
        )
        self.assertEqual(result.status, ReconciliationStatus.OK)
        self.assertFalse(result.should_pause)

    def test_summary_returns_current_state(self):
        self.reconciler.set_local_position("BTCUSDT", {
            "side": "long", "quantity": 0.01, "entry_price": 96000,
        })
        summary = self.reconciler.summary()
        self.assertEqual(summary["mode"], "paper")
        self.assertIn("BTCUSDT", summary["local_positions"])
        self.assertEqual(summary["local_positions"]["BTCUSDT"]["side"], "long")


if __name__ == "__main__":
    unittest.main()
