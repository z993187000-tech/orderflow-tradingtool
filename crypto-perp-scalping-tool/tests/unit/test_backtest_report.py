import unittest

from crypto_perp_tool.backtest import BacktestReporter


class BacktestReporterTests(unittest.TestCase):
    def test_report_summarizes_closed_positions_and_risk_metrics(self):
        details = {
            "paper": {
                "signals": [
                    {"setup": "lvn_break_acceptance"},
                    {"setup": "hvn_vah_failed_breakout"},
                    {"setup": "lvn_break_acceptance"},
                ],
                "orders": [
                    {"slippage_bps": 3.0},
                    {"slippage_bps": 12.0},
                    {"slippage_bps": 6.0},
                ],
                "closed_positions": [
                    {
                        "timestamp": 1_000,
                        "opened_at": 500,
                        "setup": "lvn_break_acceptance",
                        "entry_price": 100.0,
                        "stop_price": 95.0,
                        "target_price": 110.0,
                        "realized_pnl": 25.0,
                    },
                    {
                        "timestamp": 2_000,
                        "opened_at": 1_500,
                        "setup": "hvn_vah_failed_breakout",
                        "entry_price": 100.0,
                        "stop_price": 105.0,
                        "target_price": 90.0,
                        "realized_pnl": -10.0,
                    },
                    {
                        "timestamp": 3_000,
                        "opened_at": 2_500,
                        "setup": "lvn_break_acceptance",
                        "entry_price": 100.0,
                        "stop_price": 95.0,
                        "target_price": 110.0,
                        "realized_pnl": -5.0,
                    },
                ],
            }
        }

        report = BacktestReporter(initial_equity=10_000).from_details(details)

        self.assertEqual(report.total_trades, 3)
        self.assertAlmostEqual(report.win_rate, 1 / 3)
        self.assertAlmostEqual(report.profit_factor, 25 / 15)
        self.assertEqual(report.max_consecutive_losses, 2)
        self.assertEqual(report.net_pnl, 10.0)
        self.assertEqual(report.max_drawdown, 15.0)
        self.assertEqual(report.average_holding_time_ms, 500.0)
        self.assertEqual(report.average_slippage_bps, 7.0)
        self.assertEqual(report.by_setup["lvn_break_acceptance"]["trades"], 2)
        self.assertEqual(report.data_quality["closed_positions"], "present")

    def test_report_groups_by_setup_model_market_state_and_session(self):
        details = {
            "paper": {
                "signals": [
                    {
                        "setup": "vah_breakout_lvn_pullback_aggression",
                        "setup_model": "squeeze_continuation",
                        "market_state": "imbalanced_up",
                        "session": "london",
                    }
                ],
                "orders": [],
                "closed_positions": [
                    {
                        "timestamp": 1_000,
                        "opened_at": 500,
                        "setup": "vah_breakout_lvn_pullback_aggression",
                        "setup_model": "squeeze_continuation",
                        "market_state": "imbalanced_up",
                        "session": "london",
                        "entry_price": 100.0,
                        "stop_price": 95.0,
                        "target_price": 110.0,
                        "realized_pnl": 25.0,
                    }
                ],
            }
        }

        report = BacktestReporter(initial_equity=10_000).from_details(details)

        key = "squeeze_continuation|imbalanced_up|london"
        self.assertIn(key, report.by_strategy_context)
        self.assertEqual(report.by_strategy_context[key]["trades"], 1)
        self.assertEqual(report.by_strategy_context[key]["signals"], 1)

    def test_empty_report_marks_missing_trade_data(self):
        report = BacktestReporter(initial_equity=10_000).from_details({"paper": {}})

        self.assertEqual(report.total_trades, 0)
        self.assertEqual(report.win_rate, 0.0)
        self.assertEqual(report.profit_factor, 0.0)
        self.assertEqual(report.data_quality["closed_positions"], "missing")

    def test_report_includes_config_version_when_provided(self):
        details = {
            "paper": {
                "signals": [],
                "orders": [],
                "closed_positions": [],
            }
        }
        reporter = BacktestReporter(initial_equity=10_000, config_version="abc123def456")
        report = reporter.from_details(details)
        self.assertEqual(report.config_version, "abc123def456")

    def test_report_config_version_empty_by_default(self):
        reporter = BacktestReporter(initial_equity=10_000)
        report = reporter.from_details({"paper": {}})
        self.assertEqual(report.config_version, "")


if __name__ == "__main__":
    unittest.main()
