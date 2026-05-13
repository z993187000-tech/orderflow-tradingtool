import json
import tempfile
import unittest
from pathlib import Path

from crypto_perp_tool.execution.paper_engine import PaperExecutionConfig, PaperTradingEngine
from crypto_perp_tool.market_data import TradeEvent
from crypto_perp_tool.types import SignalSide, TradeSignal


class OneShotSignalEngine:
    def __init__(self, signal: TradeSignal) -> None:
        self.signal = signal
        self.calls = 0

    def evaluate(self, snapshot, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return self.signal
        return None


def sample_trade_events(base_time: int = 1000) -> list[TradeEvent]:
    return [
        TradeEvent(base_time + 1000, "BTCUSDT", 96000, 50, False),
        TradeEvent(base_time + 2000, "BTCUSDT", 96020, 60, False),
        TradeEvent(base_time + 3000, "BTCUSDT", 96010, 30, False),
        TradeEvent(base_time + 4000, "BTCUSDT", 95990, 20, False),
        TradeEvent(base_time + 5000, "BTCUSDT", 96030, 40, False),
        TradeEvent(base_time + 6000, "BTCUSDT", 96015, 35, False),
        TradeEvent(base_time + 7000, "BTCUSDT", 95995, 25, False),
        TradeEvent(base_time + 8000, "BTCUSDT", 96025, 45, False),
        TradeEvent(base_time + 9000, "BTCUSDT", 96008, 15, False),
        TradeEvent(base_time + 10000, "BTCUSDT", 96035, 55, False),
        TradeEvent(base_time + 11000, "BTCUSDT", 96480, 60, False),
        TradeEvent(base_time + 12000, "BTCUSDT", 96510, 70, False),
        TradeEvent(base_time + 13000, "BTCUSDT", 96490, 50, False),
        TradeEvent(base_time + 14000, "BTCUSDT", 96520, 65, False),
        TradeEvent(base_time + 15000, "BTCUSDT", 96505, 55, False),
        TradeEvent(base_time + 16000, "BTCUSDT", 96530, 50, False),
        TradeEvent(base_time + 17000, "BTCUSDT", 96485, 40, False),
        TradeEvent(base_time + 18000, "BTCUSDT", 96515, 60, False),
        TradeEvent(base_time + 19000, "BTCUSDT", 96500, 35, False),
        TradeEvent(base_time + 20000, "BTCUSDT", 96525, 45, False),
        TradeEvent(base_time + 21000, "BTCUSDT", 96110, 2, True),
        TradeEvent(base_time + 22000, "BTCUSDT", 96100, 1, True),
        TradeEvent(base_time + 23000, "BTCUSDT", 96090, 1, True),
        TradeEvent(base_time + 24000, "BTCUSDT", 96105, 1, True),
        TradeEvent(base_time + 25000, "BTCUSDT", 96120, 3, False),
        TradeEvent(base_time + 26000, "BTCUSDT", 96130, 4, False),
        TradeEvent(base_time + 27000, "BTCUSDT", 96115, 3, False),
        TradeEvent(base_time + 28000, "BTCUSDT", 96140, 5, False),
        TradeEvent(base_time + 29000, "BTCUSDT", 96125, 4, False),
        TradeEvent(base_time + 30000, "BTCUSDT", 96150, 6, False),
        TradeEvent(base_time + 31000, "BTCUSDT", 96135, 5, False),
        TradeEvent(base_time + 32000, "BTCUSDT", 96160, 7, False),
        TradeEvent(base_time + 33000, "BTCUSDT", 96145, 6, False),
        TradeEvent(base_time + 34000, "BTCUSDT", 96170, 8, False),
        TradeEvent(base_time + 35000, "BTCUSDT", 96155, 5, False),
        TradeEvent(base_time + 36000, "BTCUSDT", 96180, 9, False),
        TradeEvent(base_time + 37000, "BTCUSDT", 96165, 12, False),
        TradeEvent(base_time + 38000, "BTCUSDT", 96195, 10, False),
        TradeEvent(base_time + 39000, "BTCUSDT", 96175, 8, False),
        TradeEvent(base_time + 40000, "BTCUSDT", 96200, 15, False),
        TradeEvent(base_time + 41000, "BTCUSDT", 96185, 10, False),
        TradeEvent(base_time + 42000, "BTCUSDT", 96220, 14, False),
        TradeEvent(base_time + 43000, "BTCUSDT", 96210, 11, False),
        TradeEvent(base_time + 44000, "BTCUSDT", 96250, 12, False),
        TradeEvent(base_time + 45000, "BTCUSDT", 96230, 9, False),
        TradeEvent(base_time + 46000, "BTCUSDT", 96280, 16, False),
        TradeEvent(base_time + 47000, "BTCUSDT", 96260, 13, False),
        TradeEvent(base_time + 48000, "BTCUSDT", 96300, 18, False),
        TradeEvent(base_time + 49000, "BTCUSDT", 96320, 10, False),
        TradeEvent(base_time + 50000, "BTCUSDT", 96350, 15, False),
        TradeEvent(base_time + 51000, "BTCUSDT", 96380, 12, False),
        TradeEvent(base_time + 52000, "BTCUSDT", 96400, 20, False),
        TradeEvent(base_time + 53000, "BTCUSDT", 96430, 14, False),
        TradeEvent(base_time + 54000, "BTCUSDT", 96460, 18, False),
        TradeEvent(base_time + 55000, "BTCUSDT", 96500, 22, False),
        TradeEvent(base_time + 56000, "BTCUSDT", 96450, 15, False),
        TradeEvent(base_time + 57000, "BTCUSDT", 96540, 8, False),
        TradeEvent(base_time + 58000, "BTCUSDT", 96510, 12, False),
        TradeEvent(base_time + 59000, "BTCUSDT", 96580, 10, False),
        TradeEvent(base_time + 60000, "BTCUSDT", 96550, 6, False),
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
        signal = TradeSignal(
            id="sig-open-position",
            symbol="BTCUSDT",
            side=SignalSide.LONG,
            setup="test_open_position",
            entry_price=100,
            stop_price=90,
            target_price=130,
            confidence=0.8,
            reasons=("test",),
            invalidation_rules=("stop",),
            created_at=1_000,
        )
        engine = PaperTradingEngine(
            symbol="BTCUSDT",
            equity=10_000,
            signal_cooldown_ms=0,
            execution_config=PaperExecutionConfig(limit_entry_pullback_bps=0.0),
        )
        engine.signals = OneShotSignalEngine(signal)

        engine.process_trade(TradeEvent(1_000, "BTCUSDT", 100, 1, False), received_at=1_000)
        engine.process_trade(TradeEvent(2_000, "BTCUSDT", 100, 1, True), received_at=2_000)
        first_summary = engine.summary()
        self.assertEqual(first_summary["orders"], 1)
        self.assertIsNotNone(first_summary["open_position"])

        for event in [
            TradeEvent(18_500, "BTCUSDT", 96490, 15, False),
            TradeEvent(18_600, "BTCUSDT", 96495, 15, False),
            TradeEvent(18_700, "BTCUSDT", 96500, 15, False),
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
            signal = TradeSignal(
                id="sig-restore-open",
                symbol="BTCUSDT",
                side=SignalSide.LONG,
                setup="test_restore_open",
                entry_price=100,
                stop_price=90,
                target_price=130,
                confidence=0.8,
                reasons=("test",),
                invalidation_rules=("stop",),
                created_at=1_000,
            )
            engine = PaperTradingEngine(
                symbol="BTCUSDT",
                equity=10_000,
                signal_cooldown_ms=0,
                journal_path=journal_path,
                execution_config=PaperExecutionConfig(limit_entry_pullback_bps=0.0),
            )
            engine.signals = OneShotSignalEngine(signal)
            engine.process_trade(TradeEvent(1_000, "BTCUSDT", 100, 1, False), received_at=1_000)
            engine.process_trade(TradeEvent(2_000, "BTCUSDT", 100, 1, True), received_at=2_000)

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

        self.assertGreaterEqual(summary["orders"], 1)
        self.assertGreaterEqual(summary["closed_positions"], 1)
        self.assertEqual(summary["open_position"], None)
        self.assertGreater(summary["realized_pnl"], 0)

    def test_paper_engine_records_aggression_bubble_markers(self):
        engine = PaperTradingEngine(symbol="BTCUSDT", equity=10_000)

        engine.process_trade(TradeEvent(1_000, "BTCUSDT", 100, 55, False), received_at=1_000)

        bubble = next(marker for marker in engine.markers() if marker["type"] == "aggression_bubble")

        self.assertEqual(bubble["side"], "buy")
        self.assertEqual(bubble["tier"], "block")
        self.assertEqual(bubble["quantity"], 55)

    def test_paper_engine_moves_stop_to_break_even_after_one_and_half_r(self):
        signal = TradeSignal(
            id="sig-paper-break-even",
            symbol="BTCUSDT",
            side=SignalSide.LONG,
            setup="test_break_even",
            entry_price=100,
            stop_price=90,
            target_price=130,
            confidence=0.8,
            reasons=("test",),
            invalidation_rules=("stop",),
            created_at=1_000,
        )
        engine = PaperTradingEngine(symbol="BTCUSDT", equity=10_000, signal_cooldown_ms=0)
        engine.signals = OneShotSignalEngine(signal)

        engine.process_trade(TradeEvent(1_000, "BTCUSDT", 100, 1, False), received_at=1_000)
        engine.process_trade(TradeEvent(2_000, "BTCUSDT", 99.9, 1, True), received_at=2_000)
        engine.process_trade(TradeEvent(3_000, "BTCUSDT", 116, 1, False), received_at=3_000)

        summary = engine.summary()
        position = summary["open_position"]

        self.assertIsNotNone(position)
        self.assertGreaterEqual(position["stop_price"], position["entry_price"])
        self.assertTrue(any(action["action"] == "break_even_shift" for action in summary["protective_actions"]))

    def test_limit_entry_waits_for_pullback_before_opening_position(self):
        signal = TradeSignal(
            id="sig-limit-entry",
            symbol="BTCUSDT",
            side=SignalSide.LONG,
            setup="test_limit_entry",
            entry_price=100,
            stop_price=90,
            target_price=130,
            confidence=0.8,
            reasons=("test",),
            invalidation_rules=("stop",),
            created_at=1_000,
        )
        engine = PaperTradingEngine(
            symbol="BTCUSDT",
            equity=10_000,
            signal_cooldown_ms=0,
            execution_config=PaperExecutionConfig(limit_entry_pullback_bps=100.0),
        )
        engine.signals = OneShotSignalEngine(signal)

        engine.process_trade(TradeEvent(1_000, "BTCUSDT", 100, 1, False), received_at=1_000)
        self.assertIsNone(engine.summary()["open_position"])
        self.assertEqual(engine.summary()["orders"], 0)

        engine.process_trade(TradeEvent(2_000, "BTCUSDT", 99, 1, True), received_at=2_000)
        summary = engine.summary()
        order = engine.details()["paper"]["orders"][0]

        self.assertIsNotNone(summary["open_position"])
        self.assertEqual(summary["orders"], 1)
        self.assertEqual(order["entry_order_type"], "limit")
        self.assertEqual(order["status"], "filled")
        self.assertLessEqual(summary["open_position"]["entry_price"], 99)

    def test_partial_take_profit_then_trailing_stop_manages_remaining_position(self):
        signal = TradeSignal(
            id="sig-partial-trail",
            symbol="BTCUSDT",
            side=SignalSide.LONG,
            setup="test_partial_trail",
            entry_price=100,
            stop_price=90,
            target_price=130,
            confidence=0.8,
            reasons=("test",),
            invalidation_rules=("stop",),
            created_at=1_000,
        )
        engine = PaperTradingEngine(
            symbol="BTCUSDT",
            equity=10_000,
            signal_cooldown_ms=0,
            execution_config=PaperExecutionConfig(limit_entry_pullback_bps=0.0),
        )
        engine.signals = OneShotSignalEngine(signal)

        engine.process_trade(TradeEvent(1_000, "BTCUSDT", 100, 1, False), received_at=1_000)
        engine.process_trade(TradeEvent(2_000, "BTCUSDT", 100, 1, True), received_at=2_000)
        original_quantity = engine.summary()["open_position"]["quantity"]

        engine.process_trade(TradeEvent(3_000, "BTCUSDT", 110, 1, False), received_at=3_000)
        after_partial = engine.summary()["open_position"]
        partial = engine.details()["paper"]["closed_positions"][0]

        self.assertEqual(partial["exit_reason"], "partial_take_profit")
        self.assertLess(after_partial["quantity"], original_quantity)
        self.assertGreaterEqual(after_partial["stop_price"], after_partial["entry_price"])

        engine.process_trade(TradeEvent(4_000, "BTCUSDT", 112, 1, False), received_at=4_000)
        trail_stop = engine.summary()["open_position"]["stop_price"]
        engine.process_trade(TradeEvent(5_000, "BTCUSDT", trail_stop - 0.1, 1, True), received_at=5_000)
        closed = engine.details()["paper"]["closed_positions"][-1]

        self.assertIsNone(engine.summary()["open_position"])
        self.assertEqual(closed["exit_reason"], "trailing_stop")

    def test_orderflow_invalidation_can_exit_without_minimum_hold_time(self):
        signal = TradeSignal(
            id="sig-orderflow-exit",
            symbol="BTCUSDT",
            side=SignalSide.LONG,
            setup="test_orderflow_exit",
            entry_price=100,
            stop_price=90,
            target_price=130,
            confidence=0.8,
            reasons=("test",),
            invalidation_rules=("delta flips negative",),
            created_at=1_000,
        )
        engine = PaperTradingEngine(
            symbol="BTCUSDT",
            equity=10_000,
            signal_cooldown_ms=0,
            execution_config=PaperExecutionConfig(limit_entry_pullback_bps=0.0),
        )
        engine.signals = OneShotSignalEngine(signal)

        engine.process_trade(TradeEvent(1_000, "BTCUSDT", 100, 1, False), received_at=1_000)
        engine.process_trade(TradeEvent(2_000, "BTCUSDT", 100, 1, True), received_at=2_000)
        engine.process_trade(TradeEvent(2_100, "BTCUSDT", 99.8, 50, True), received_at=2_100)

        closed = engine.details()["paper"]["closed_positions"][-1]
        self.assertIsNone(engine.summary()["open_position"])
        self.assertEqual(closed["exit_reason"], "orderflow_invalidation")
        self.assertLess(closed["timestamp"] - closed["opened_at"], 2_000)


if __name__ == "__main__":
    unittest.main()
