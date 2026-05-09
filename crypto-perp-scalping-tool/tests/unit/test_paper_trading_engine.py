import json
import tempfile
import unittest
from pathlib import Path

from crypto_perp_tool.execution.paper_engine import PaperTradingEngine
from crypto_perp_tool.market_data import TradeEvent


def sample_trade_events(base_time: int = 1000) -> list[TradeEvent]:
    return [
        TradeEvent(base_time + 1000, "BTCUSDT", 100, 5, True),
        TradeEvent(base_time + 2000, "BTCUSDT", 110, 20, False),
        TradeEvent(base_time + 3000, "BTCUSDT", 120, 3, True),
        TradeEvent(base_time + 4000, "BTCUSDT", 130, 5, True),
        TradeEvent(base_time + 5000, "BTCUSDT", 140, 30, False),
        TradeEvent(base_time + 6000, "BTCUSDT", 150, 5, True),
        TradeEvent(base_time + 7000, "BTCUSDT", 126, 12, False),
        TradeEvent(base_time + 8000, "BTCUSDT", 141, 10, False),
    ]


class PaperTradingEngineTests(unittest.TestCase):
    def test_processes_trade_stream_into_signal_order_and_closed_position(self):
        engine = PaperTradingEngine(symbol="BTCUSDT", equity=10_000, signal_cooldown_ms=0)

        for event in sample_trade_events():
            engine.process_trade(event, received_at=event.timestamp)

        summary = engine.summary()
        details = engine.details()
        paper = details["paper"]

        self.assertGreaterEqual(summary["signals"], 1)
        self.assertGreaterEqual(summary["orders"], 1)
        self.assertGreaterEqual(summary["closed_positions"], 1)
        self.assertGreater(summary["realized_pnl"], 0)
        self.assertEqual(summary["open_position"], None)
        self.assertEqual(len(paper["signals"]), summary["signals"])
        self.assertEqual(len(paper["orders"]), summary["orders"])
        self.assertEqual(len(paper["closed_positions"]), summary["closed_positions"])
        self.assertGreater(paper["pnl_by_range"]["all"], 0)

        marker_types = {marker["type"] for marker in engine.markers()}
        self.assertIn("signal", marker_types)
        self.assertIn("position_closed", marker_types)

    def test_does_not_open_a_new_order_while_position_is_open(self):
        engine = PaperTradingEngine(symbol="BTCUSDT", equity=10_000, signal_cooldown_ms=0)

        for event in sample_trade_events()[:-1]:
            engine.process_trade(event, received_at=event.timestamp)
        first_summary = engine.summary()
        self.assertEqual(first_summary["orders"], 1)
        self.assertIsNotNone(first_summary["open_position"])

        for event in [
            TradeEvent(7100, "BTCUSDT", 127, 15, False),
            TradeEvent(7200, "BTCUSDT", 128, 15, False),
            TradeEvent(7300, "BTCUSDT", 129, 15, False),
        ]:
            engine.process_trade(event, received_at=event.timestamp)

        self.assertEqual(engine.summary()["orders"], 1)

    def test_ignores_other_symbols(self):
        engine = PaperTradingEngine(symbol="BTCUSDT", equity=10_000)

        engine.process_trade(TradeEvent(1000, "ETHUSDT", 2000, 1, False), received_at=1000)

        self.assertEqual(engine.summary()["profile_trade_count"], 0)
        self.assertEqual(engine.summary()["signals"], 0)

    def test_rejects_stale_live_market_data(self):
        engine = PaperTradingEngine(symbol="BTCUSDT", equity=10_000, signal_cooldown_ms=0)

        for event in sample_trade_events():
            engine.process_trade(event, received_at=event.timestamp + 3_000)

        self.assertEqual(engine.summary()["signals"], 0)
        self.assertGreaterEqual(engine.summary()["data_lag_ms"], 3_000)

    def test_delta_windows_use_seconds_instead_of_trade_count(self):
        engine = PaperTradingEngine(symbol="BTCUSDT", equity=10_000, signal_cooldown_ms=0)

        engine.process_trade(TradeEvent(1_000, "BTCUSDT", 100, 100, False), received_at=1_000)
        engine.process_trade(TradeEvent(41_000, "BTCUSDT", 101, 3, True), received_at=41_000)

        summary = engine.summary()

        self.assertEqual(summary["delta_30s"], -3)
        self.assertEqual(summary["delta_60s"], 97)

    def test_profile_window_uses_rolling_time_not_all_seen_trades(self):
        engine = PaperTradingEngine(symbol="BTCUSDT", equity=10_000, signal_cooldown_ms=0)
        old = TradeEvent(1_000, "BTCUSDT", 100, 500, False)
        recent = TradeEvent(5 * 60 * 60 * 1000, "BTCUSDT", 200, 1, False)

        engine.process_trade(old, received_at=old.timestamp)
        engine.process_trade(recent, received_at=recent.timestamp)

        summary = engine.summary()

        self.assertEqual(summary["seen_trade_count"], 2)
        self.assertEqual(summary["profile_trade_count"], 1)

    def test_writes_live_paper_events_to_journal(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal_path = Path(tmp) / "live-paper.jsonl"
            engine = PaperTradingEngine(symbol="BTCUSDT", equity=10_000, signal_cooldown_ms=0, journal_path=journal_path)

            for event in sample_trade_events():
                engine.process_trade(event, received_at=event.timestamp)

            rows = [json.loads(line) for line in journal_path.read_text(encoding="utf-8").splitlines()]

        event_types = [row["type"] for row in rows]
        self.assertIn("signal", event_types)
        self.assertIn("risk_decision", event_types)
        self.assertIn("paper_order", event_types)
        self.assertIn("paper_fill", event_types)
        self.assertIn("position_closed", event_types)

    def test_restores_open_position_from_live_paper_journal(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal_path = Path(tmp) / "live-paper.jsonl"
            engine = PaperTradingEngine(symbol="BTCUSDT", equity=10_000, signal_cooldown_ms=0, journal_path=journal_path)
            for event in sample_trade_events()[:-1]:
                engine.process_trade(event, received_at=event.timestamp)

            restored = PaperTradingEngine(symbol="BTCUSDT", equity=10_000, signal_cooldown_ms=0, journal_path=journal_path)
            summary = restored.summary()

        self.assertEqual(summary["orders"], 1)
        self.assertIsNotNone(summary["open_position"])
        self.assertEqual(summary["closed_positions"], 0)

    def test_restores_closed_position_and_pnl_from_live_paper_journal(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal_path = Path(tmp) / "live-paper.jsonl"
            engine = PaperTradingEngine(symbol="BTCUSDT", equity=10_000, signal_cooldown_ms=0, journal_path=journal_path)
            for event in sample_trade_events():
                engine.process_trade(event, received_at=event.timestamp)

            restored = PaperTradingEngine(symbol="BTCUSDT", equity=10_000, signal_cooldown_ms=0, journal_path=journal_path)
            summary = restored.summary()

        self.assertEqual(summary["orders"], 1)
        self.assertEqual(summary["closed_positions"], 1)
        self.assertEqual(summary["open_position"], None)
        self.assertGreater(summary["realized_pnl"], 0)


if __name__ == "__main__":
    unittest.main()
