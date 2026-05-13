import unittest

from crypto_perp_tool.backtest.engine import BacktestConfig, BacktestEngine, BacktestResult
from crypto_perp_tool.backtest.report import BacktestReport
from crypto_perp_tool.market_data import TradeEvent


def sample_events(base_time: int = 1000000, count: int = 50) -> list[TradeEvent]:
    """Generate synthetic trade events that oscillate around a price."""
    events = []
    price = 96000.0
    for i in range(count):
        price += 10.0 if i % 5 != 0 else -12.0
        events.append(TradeEvent(
            timestamp=base_time + i * 2000,
            symbol="BTCUSDT",
            price=price,
            quantity=2.0 + (i % 3),
            is_buyer_maker=(i % 3 == 0),
        ))
    return events


class BacktestEngineTests(unittest.TestCase):
    def test_empty_events_returns_empty_result(self):
        engine = BacktestEngine(BacktestConfig())
        result = engine.run([])
        self.assertEqual(result.total_events, 0)
        self.assertEqual(len(result.errors), 1)
        self.assertIn("no events", result.errors[0])

    def test_smoke_with_synthetic_events(self):
        events = sample_events(count=100)
        engine = BacktestEngine(BacktestConfig(symbol="BTCUSDT", equity=10_000))
        result = engine.run(events)
        self.assertGreaterEqual(result.total_events, 100)
        self.assertEqual(result.symbol, "BTCUSDT")
        self.assertEqual(result.equity_start, 10_000)
        self.assertIsInstance(result.report, BacktestReport)
        self.assertTrue(hasattr(result, "equity_curve"))
        self.assertGreater(len(result.equity_curve), 0)

    def test_equity_curve_length_matches_events(self):
        events = sample_events(count=20)
        engine = BacktestEngine(BacktestConfig())
        result = engine.run(events)
        # equity_curve has initial equity + one entry per event
        self.assertEqual(len(result.equity_curve), len(events) + 1)

    def test_config_version_is_set(self):
        engine = BacktestEngine(BacktestConfig())
        result = engine.run(sample_events(count=20))
        self.assertTrue(len(result.config_version) > 0)

    def test_time_filter_respected(self):
        events = sample_events(base_time=1000000, count=50)
        config = BacktestConfig(start_ms=1005000, end_ms=1010000)
        engine = BacktestEngine(config)
        result = engine.run(events)
        filtered_count = sum(1 for e in events if 1005000 <= e.timestamp <= 1010000)
        self.assertEqual(result.total_events, filtered_count)

    def test_split_run_requires_valid_fractions(self):
        with self.assertRaises(ValueError):
            BacktestEngine(BacktestConfig(is_fraction=0.0, oos_fraction=0.0)).run_split([])

    def test_split_run_partitions_events(self):
        events = sample_events(base_time=1000000, count=60)
        config = BacktestConfig(
            symbol="BTCUSDT", equity=10_000,
            is_fraction=0.5, oos_fraction=0.5,
        )
        engine = BacktestEngine(config)
        is_result, oos_result = engine.run_split(events)
        self.assertTrue(is_result.is_in_sample)
        self.assertFalse(oos_result.is_in_sample)
        total = is_result.total_events + oos_result.total_events
        self.assertEqual(total, len(events))

    def test_load_csv_rejects_missing_columns(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "bad.csv"
            csv_path.write_text("timestamp,price\n1000,96000\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                BacktestEngine.load_csv(csv_path)


if __name__ == "__main__":
    unittest.main()
