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


class NoSignalEngine:
    last_reject_reasons: tuple[str, ...] = ()

    def evaluate(self, snapshot, **kwargs):
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
        signal = TradeSignal(
            id="sig-stream-1",
            symbol="BTCUSDT",
            side=SignalSide.LONG,
            setup="test_stream",
            entry_price=96010,
            stop_price=95950,
            target_price=96070,
            confidence=0.8,
            reasons=("test",),
            invalidation_rules=("stop",),
            created_at=3_000,
        )
        engine = PaperTradingEngine(
            symbol="BTCUSDT",
            equity=10_000,
            signal_cooldown_ms=0,
            execution_config=PaperExecutionConfig(),
        )
        engine.signals = OneShotSignalEngine(signal)

        for event in sample_trade_events():
            engine.process_trade(event, received_at=event.timestamp)

        summary = engine.summary()
        details = engine.details()
        paper = details["paper"]

        self.assertGreaterEqual(summary["signals"], 1)
        self.assertGreaterEqual(summary["orders"], 1)
        self.assertGreaterEqual(summary["closed_positions"], 1)
        self.assertNotEqual(summary["realized_pnl"], 0)
        self.assertEqual(summary["open_position"], None)
        self.assertEqual(len(paper["signals"]), summary["signals"])
        self.assertEqual(len(paper["orders"]), summary["orders"])
        self.assertEqual(len(paper["closed_positions"]), summary["closed_positions"])
        self.assertNotEqual(paper["pnl_by_range"]["all"], 0)

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

    def test_profile_levels_use_execution_micro_and_context_windows(self):
        engine = PaperTradingEngine(symbol="BTCUSDT", equity=10_000, signal_cooldown_ms=0)
        engine.signals = NoSignalEngine()
        now = 120 * 60 * 1000

        def add_batch(start: int, count: int, price_base: float, spacing_ms: int = 1000, quantity: float = 1) -> None:
            for index in range(count):
                price = price_base + (index % 3) * 20
                timestamp = start + index * spacing_ms
                engine.process_trade(TradeEvent(timestamp, "BTCUSDT", price, quantity, False), received_at=timestamp)

        add_batch(now - 31 * 60 * 1000, 10, 100, quantity=20)
        add_batch(now - 16 * 60 * 1000, 25, 200)
        add_batch(now - 14 * 60 * 1000, 50, 300)

        levels = engine._profile_levels(now)
        execution_prices = {level.price for level in levels if level.window == "execution_30m"}
        micro_prices = {level.price for level in levels if level.window == "micro_15m"}
        context_prices = {level.price for level in levels if level.window == "context_60m"}

        self.assertIn("execution_30m", {level.window for level in levels})
        self.assertIn("micro_15m", {level.window for level in levels})
        self.assertIn("context_60m", {level.window for level in levels})
        self.assertTrue(all(price >= 200 for price in execution_prices))
        self.assertTrue(all(price >= 300 for price in micro_prices))
        self.assertTrue(any(price < 200 for price in context_prices))

    def test_writes_live_paper_events_to_journal(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal_path = Path(tmp) / "live-paper.jsonl"
            signal = TradeSignal(
                id="sig-journal-1",
                symbol="BTCUSDT",
                side=SignalSide.LONG,
                setup="test_journal",
                entry_price=96010,
                stop_price=95980,
                target_price=96160,
                confidence=0.8,
                reasons=("test",),
                invalidation_rules=("stop",),
                created_at=3_000,
            )
            engine = PaperTradingEngine(symbol="BTCUSDT", equity=10_000, signal_cooldown_ms=0, journal_path=journal_path)
            engine.signals = OneShotSignalEngine(signal)

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
            signal = TradeSignal(
                id="sig-restore-closed-1",
                symbol="BTCUSDT",
                side=SignalSide.LONG,
                setup="test_restore_closed",
                entry_price=96010,
                stop_price=95980,
                target_price=96160,
                confidence=0.8,
                reasons=("test",),
                invalidation_rules=("stop",),
                created_at=3_000,
            )
            engine = PaperTradingEngine(symbol="BTCUSDT", equity=10_000, signal_cooldown_ms=0, journal_path=journal_path)
            engine.signals = OneShotSignalEngine(signal)
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

    def test_paper_engine_moves_stop_to_net_breakeven_after_two_and_half_r(self):
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
        # Trigger 2.5R: entry=100, stop=90, risk=10, 2.5R = 25 favorable move, so price = 125
        engine.process_trade(TradeEvent(3_000, "BTCUSDT", 126, 1, False), received_at=3_000)

        summary = engine.summary()
        position = summary["open_position"]

        self.assertIsNotNone(position)
        # Net breakeven stop: entry + estimated round trip cost
        self.assertGreater(position["stop_price"], position["entry_price"])
        self.assertTrue(any(action["action"] == "break_even_shift" for action in summary["protective_actions"]))

    def test_paper_engine_uses_signal_reward_risk_for_breakeven_after_entry(self):
        signal = TradeSignal(
            id="sig-paper-entry-r",
            symbol="BTCUSDT",
            side=SignalSide.LONG,
            setup="test_entry_r",
            entry_price=100,
            stop_price=90,
            target_price=180,
            confidence=0.8,
            reasons=("test", "target 8.0R"),
            invalidation_rules=("stop",),
            created_at=1_000,
            target_r_multiple=8.0,
        )
        engine = PaperTradingEngine(
            symbol="BTCUSDT",
            equity=10_000,
            signal_cooldown_ms=0,
            execution_config=PaperExecutionConfig(limit_entry_pullback_bps=0.0, reward_risk=3.0),
        )
        engine.signals = OneShotSignalEngine(signal)

        engine.process_trade(TradeEvent(1_000, "BTCUSDT", 100, 1, False), received_at=1_000)
        engine.process_trade(TradeEvent(2_000, "BTCUSDT", 100, 1, True), received_at=2_000)
        position = engine.summary()["open_position"]
        self.assertIsNotNone(position)
        self.assertEqual(position["target_r_multiple"], 8.0)

        engine.process_trade(TradeEvent(3_000, "BTCUSDT", 125, 1, False), received_at=3_000)
        position = engine.summary()["open_position"]
        self.assertIsNotNone(position)
        self.assertFalse(position["break_even_shifted"])
        self.assertEqual(position["stop_price"], 90)

        engine.process_trade(TradeEvent(4_000, "BTCUSDT", 141, 1, False), received_at=4_000)
        position = engine.summary()["open_position"]
        self.assertIsNotNone(position)
        self.assertTrue(position["break_even_shifted"])
        self.assertGreater(position["stop_price"], position["entry_price"])

    def test_no_partial_take_profit_in_short_term_model(self):
        signal = TradeSignal(
            id="sig-no-partial",
            symbol="BTCUSDT",
            side=SignalSide.LONG,
            setup="test_no_partial",
            entry_price=100,
            stop_price=90,
            target_price=150,  # 5R target
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

        # Price goes to 1R (110) - should NOT trigger partial TP
        engine.process_trade(TradeEvent(3_000, "BTCUSDT", 110, 1, False), received_at=3_000)
        after_1r = engine.summary()["open_position"]

        self.assertEqual(len(engine.details()["paper"]["closed_positions"]), 0)
        self.assertEqual(after_1r["quantity"], original_quantity)

    def test_paper_engine_keeps_signal_target_fixed_while_position_is_open(self):
        signal = TradeSignal(
            id="sig-fixed-target",
            symbol="BTCUSDT",
            side=SignalSide.LONG,
            setup="test_fixed_target",
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
        engine.process_trade(TradeEvent(3_000, "BTCUSDT", 125, 1, False), received_at=3_000)

        summary = engine.summary()

        self.assertIsNotNone(summary["open_position"])
        self.assertEqual(summary["open_position"]["target_price"], 130)
        self.assertEqual(len(engine.details()["paper"]["closed_positions"]), 0)

    def test_paper_engine_moves_long_stop_after_three_complete_bullish_1m_bars(self):
        signal = TradeSignal(
            id="sig-1m-stop-long",
            symbol="BTCUSDT",
            side=SignalSide.LONG,
            setup="test_1m_stop",
            entry_price=100,
            stop_price=90,
            target_price=200,
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

        for event in [
            TradeEvent(1_000, "BTCUSDT", 100, 1, False),
            TradeEvent(2_000, "BTCUSDT", 100, 1, True),
            TradeEvent(60_000, "BTCUSDT", 100, 1, False),
            TradeEvent(90_000, "BTCUSDT", 104, 1, False),
            TradeEvent(120_000, "BTCUSDT", 104, 1, False),
            TradeEvent(150_000, "BTCUSDT", 108, 1, False),
            TradeEvent(180_000, "BTCUSDT", 108, 1, False),
            TradeEvent(210_000, "BTCUSDT", 112, 1, False),
            TradeEvent(240_000, "BTCUSDT", 113, 1, False),
        ]:
            engine.process_trade(event, received_at=event.timestamp)

        position = engine.summary()["open_position"]

        self.assertIsNotNone(position)
        self.assertEqual(position["stop_price"], 104)
        self.assertTrue(
            any(action["action"] == "kline_momentum_stop_shift" for action in engine.summary()["protective_actions"])
        )

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
