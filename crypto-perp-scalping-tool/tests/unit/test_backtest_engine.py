import unittest

from crypto_perp_tool.backtest.engine import BacktestConfig, BacktestEngine, BacktestResult
from crypto_perp_tool.backtest.report import BacktestReport
from crypto_perp_tool.market_data import KlineEvent, TradeEvent


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


def sample_klines(base_time: int = 960000, count: int = 20) -> list[KlineEvent]:
    klines = []
    price = 96000.0
    for i in range(count):
        opened = price
        high = opened + 25.0 + (i % 4)
        low = opened - 18.0 - (i % 3)
        close = opened + (8.0 if i % 2 == 0 else -6.0)
        klines.append(KlineEvent(
            timestamp=base_time + i * 60_000,
            close_time=base_time + (i + 1) * 60_000 - 1,
            symbol="BTCUSDT",
            interval="1m",
            open=opened,
            high=high,
            low=low,
            close=close,
            volume=20.0 + i,
            quote_volume=(20.0 + i) * close,
            trade_count=10 + i,
            is_closed=True,
        ))
        price = close
    return klines


class BacktestEngineTests(unittest.TestCase):
    def test_empty_events_returns_empty_result(self):
        engine = BacktestEngine(BacktestConfig())
        result = engine.run([])
        self.assertEqual(result.total_events, 0)
        self.assertEqual(len(result.errors), 1)
        self.assertIn("no events", result.errors[0])

    def test_smoke_with_synthetic_events(self):
        events = sample_klines(count=100)
        engine = BacktestEngine(BacktestConfig(symbol="BTCUSDT", equity=10_000))
        result = engine.run(events)
        self.assertGreaterEqual(result.total_events, 100)
        self.assertEqual(result.symbol, "BTCUSDT")
        self.assertEqual(result.equity_start, 10_000)
        self.assertIsInstance(result.report, BacktestReport)
        self.assertTrue(hasattr(result, "equity_curve"))
        self.assertGreater(len(result.equity_curve), 0)

    def test_equity_curve_length_matches_events(self):
        events = sample_klines(count=20)
        engine = BacktestEngine(BacktestConfig())
        result = engine.run(events)
        # equity_curve has initial equity + one entry per kline
        self.assertEqual(len(result.equity_curve), len(events) + 1)

    def test_run_aggregates_trade_events_to_klines_before_simulating(self):
        events = sample_events(base_time=1_000_000, count=60)
        engine = BacktestEngine(BacktestConfig())

        result = engine.run(events)

        expected_klines = len({event.timestamp // 60_000 for event in events})
        self.assertEqual(result.total_events, expected_klines)
        self.assertEqual(len(result.equity_curve), expected_klines + 1)
        self.assertEqual(result.data_quality, "kline_1m")

    def test_config_version_is_set(self):
        engine = BacktestEngine(BacktestConfig())
        result = engine.run(sample_events(count=20))
        self.assertTrue(len(result.config_version) > 0)

    def test_time_filter_respected(self):
        events = sample_klines(base_time=960000, count=10)
        config = BacktestConfig(start_ms=1020000, end_ms=1200000)
        engine = BacktestEngine(config)
        result = engine.run(events)
        filtered_count = sum(1 for e in events if 1020000 <= e.timestamp <= 1200000)
        self.assertEqual(result.total_events, filtered_count)

    def test_split_run_requires_valid_fractions(self):
        with self.assertRaises(ValueError):
            BacktestEngine(BacktestConfig(is_fraction=0.0, oos_fraction=0.0)).run_split([])

    def test_split_run_partitions_events(self):
        events = sample_klines(base_time=960000, count=60)
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

    def test_load_csv_aggregates_trade_rows_to_klines(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "trades.csv"
            csv_path.write_text(
                "\n".join([
                    "timestamp,symbol,price,quantity,is_buyer_maker",
                    "60000,BTCUSDT,100,2,false",
                    "70000,BTCUSDT,105,3,true",
                    "119000,BTCUSDT,101,4,false",
                    "120000,BTCUSDT,102,5,false",
                    "179000,BTCUSDT,99,6,true",
                ]),
                encoding="utf-8",
            )

            klines = BacktestEngine.load_csv(csv_path)

        self.assertEqual(len(klines), 2)
        self.assertIsInstance(klines[0], KlineEvent)
        self.assertEqual(klines[0].timestamp, 60_000)
        self.assertEqual(klines[0].open, 100)
        self.assertEqual(klines[0].high, 105)
        self.assertEqual(klines[0].low, 100)
        self.assertEqual(klines[0].close, 101)
        self.assertEqual(klines[0].volume, 9)
        self.assertEqual(klines[0].trade_count, 3)

    def test_load_csv_reads_ohlcv_rows_as_klines(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "klines.csv"
            csv_path.write_text(
                "\n".join([
                    "timestamp,close_time,symbol,interval,open,high,low,close,volume,quote_volume,trade_count",
                    "60000,119999,BTCUSDT,1m,100,105,99,103,12,1236,8",
                ]),
                encoding="utf-8",
            )

            klines = BacktestEngine.load_csv(csv_path)

        self.assertEqual(len(klines), 1)
        self.assertEqual(klines[0].open, 100)
        self.assertEqual(klines[0].close_time, 119999)
        self.assertEqual(klines[0].interval, "1m")


if __name__ == "__main__":
    unittest.main()
