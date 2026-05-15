import unittest

from crypto_perp_tool.simulation import SimulationRunner, default_fault_scenarios


class SimulationRunnerTests(unittest.TestCase):
    def test_default_fault_scenarios_cover_required_cases(self):
        names = {scenario.name for scenario in default_fault_scenarios()}

        self.assertEqual(
            names,
            {
                "websocket_disconnect",
                "slippage_expansion",
                "fast_reversal",
                "partial_fill",
                "stop_submission_failure",
            },
        )

    def test_websocket_disconnect_scenario_halts_new_entries(self):
        scenario = next(item for item in default_fault_scenarios() if item.name == "websocket_disconnect")

        result = SimulationRunner().run(scenario)

        self.assertIn("halt_new_entries", result.protective_actions)
        self.assertIn("data_stale", result.reject_reasons)
        self.assertEqual(result.report.total_trades, 0)

    def test_slippage_expansion_scenario_reports_high_average_slippage(self):
        scenario = next(item for item in default_fault_scenarios() if item.name == "slippage_expansion")

        result = SimulationRunner().run(scenario)

        self.assertGreaterEqual(result.report.total_trades, 1)
        self.assertGreaterEqual(result.report.average_slippage_bps, 20.0)
        self.assertIn("slippage_expanded", result.risk_events)

    def test_fast_reversal_scenario_closes_at_loss(self):
        scenario = next(item for item in default_fault_scenarios() if item.name == "fast_reversal")

        result = SimulationRunner().run(scenario)

        self.assertGreaterEqual(result.report.total_trades, 1)
        self.assertNotEqual(result.report.net_pnl, 0)
        self.assertIn("fast_reversal_stop_hit", result.risk_events)

    def test_partial_fill_scenario_records_partial_order_status(self):
        scenario = next(item for item in default_fault_scenarios() if item.name == "partial_fill")

        result = SimulationRunner().run(scenario)
        orders = result.details["paper"]["orders"]

        self.assertEqual(orders[0]["status"], "partially_filled")
        self.assertAlmostEqual(orders[0]["fill_ratio"], 0.4)
        self.assertIn("partial_fill", result.risk_events)

    def test_stop_submission_failure_protectively_closes_and_trips_circuit(self):
        scenario = next(item for item in default_fault_scenarios() if item.name == "stop_submission_failure")

        result = SimulationRunner().run(scenario)

        self.assertIn("protective_close", result.protective_actions)
        self.assertIn("stop_submission_failed", result.risk_events)
        self.assertIn("circuit_breaker_tripped", result.risk_events)
        self.assertIsNone(result.summary["open_position"])


if __name__ == "__main__":
    unittest.main()
