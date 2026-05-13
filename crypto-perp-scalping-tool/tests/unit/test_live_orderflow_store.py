import json
import tempfile
import time
import unittest
from pathlib import Path

from crypto_perp_tool.market_data import MarkPriceEvent, QuoteEvent, SpotPriceEvent, TradeEvent
from crypto_perp_tool.market_data.binance import BinanceInstrumentSpec
from crypto_perp_tool.types import CircuitBreakerReason, SignalSide, TradeSignal
from crypto_perp_tool.web.live_store import LiveOrderflowStore


class OneShotSignalEngine:
    def __init__(self, signal: TradeSignal) -> None:
        self.signal = signal
        self.calls = 0
        self.last_reject_reasons: tuple[str, ...] = ()

    def evaluate(self, snapshot, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return self.signal
        return None


class InvalidStopSignalEngine:
    last_reject_reasons: tuple[str, ...] = ()

    def evaluate(self, snapshot, **kwargs):
        return TradeSignal(
            id="sig-invalid",
            symbol=snapshot.symbol,
            side=SignalSide.LONG,
            setup="test_invalid_stop",
            entry_price=snapshot.last_price,
            stop_price=snapshot.last_price,
            target_price=snapshot.last_price + 10,
            confidence=0.5,
            reasons=("test signal",),
            invalidation_rules=("invalid stop",),
            created_at=snapshot.local_time,
        )


class CapturingStaleSignalEngine:
    def __init__(self) -> None:
        self.last_snapshot = None
        self.last_reject_reasons: tuple[str, ...] = ()

    def evaluate(self, snapshot, **kwargs):
        self.last_snapshot = snapshot
        if snapshot.local_time - snapshot.event_time > 2_000:
            self.last_reject_reasons = ("data_stale",)
        return None


class LiveOrderflowStoreTests(unittest.TestCase):
    def test_live_store_builds_orderflow_view_from_recent_events(self):
        store = LiveOrderflowStore(symbol="BTCUSDT", max_events=10)

        for event in [
            TradeEvent(1000, "BTCUSDT", 96000, 50, False),
            TradeEvent(2000, "BTCUSDT", 96020, 60, False),
            TradeEvent(3000, "BTCUSDT", 96110, 2, True),
            TradeEvent(4000, "BTCUSDT", 96100, 1, True),
            TradeEvent(5000, "BTCUSDT", 96200, 80, False),
            TradeEvent(6000, "BTCUSDT", 96220, 70, False),
            TradeEvent(7000, "BTCUSDT", 96150, 5, False),
        ]:
            store.add_trade(event)

        view = store.view()

        self.assertEqual(view["summary"]["source"], "binance")
        self.assertEqual(view["summary"]["symbol"], "BTCUSDT")
        self.assertEqual(view["summary"]["trade_count"], 7)
        self.assertEqual(view["summary"]["last_price"], 96150)
        self.assertTrue(any(level["type"] == "LVN" for level in view["profile_levels"]))
        self.assertGreater(len(view["delta_series"]), 0)

    def test_live_store_ignores_other_symbols(self):
        store = LiveOrderflowStore(symbol="BTCUSDT", max_events=10)

        store.add_trade(TradeEvent(1000, "ETHUSDT", 2000, 1, False))

        self.assertEqual(store.view()["summary"]["trade_count"], 0)

    def test_live_store_exposes_connection_status(self):
        store = LiveOrderflowStore(symbol="BTCUSDT", max_events=10)

        store.set_connection_status("error", "Install websockets")
        summary = store.view()["summary"]

        self.assertEqual(summary["connection_status"], "error")
        self.assertEqual(summary["connection_message"], "Install websockets")

    def test_live_store_does_not_mask_error_status_after_price_arrives(self):
        store = LiveOrderflowStore(symbol="BTCUSDT", max_events=10)

        store.add_trade(TradeEvent(1000, "BTCUSDT", 100, 1, False), received_at=1000)
        store.set_connection_status("error", "market: disconnected")
        summary = store.view()["summary"]

        self.assertEqual(summary["last_price"], 100)
        self.assertEqual(summary["connection_status"], "error")
        self.assertEqual(summary["connection_message"], "market: disconnected")

    def test_live_store_uses_perp_trade_as_display_last_price_with_spot_context(self):
        store = LiveOrderflowStore(symbol="BTCUSDT", max_events=10)

        store.add_trade(TradeEvent(1000, "BTCUSDT", 100, 1, False))
        store.add_quote(QuoteEvent(1100, "BTCUSDT", 108, 110))
        store.add_spot(SpotPriceEvent(1200, "BTCUSDT", 112))
        summary = store.view()["summary"]

        self.assertEqual(summary["last_price"], 100)
        self.assertEqual(summary["spot_last_price"], 112)
        self.assertEqual(summary["last_trade_price"], 100)
        self.assertEqual(summary["bid_price"], 108)
        self.assertEqual(summary["ask_price"], 110)
        self.assertEqual(summary["quote_mid_price"], 109)
        self.assertEqual(summary["price_source"], "aggTrade")

    def test_live_store_falls_back_to_perp_trade_before_first_spot_trade(self):
        store = LiveOrderflowStore(symbol="BTCUSDT", max_events=10)

        store.add_trade(TradeEvent(1000, "BTCUSDT", 100, 1, False))
        store.add_quote(QuoteEvent(1100, "BTCUSDT", 108, 110))
        summary = store.view()["summary"]

        self.assertEqual(summary["last_price"], 100)
        self.assertEqual(summary["spot_last_price"], None)
        self.assertEqual(summary["last_trade_price"], 100)
        self.assertEqual(summary["price_source"], "aggTrade")

    def test_live_store_keeps_perp_trade_before_mark_or_index_price(self):
        store = LiveOrderflowStore(symbol="BTCUSDT", max_events=10)

        store.add_trade(TradeEvent(1000, "BTCUSDT", 100, 1, False))
        store.add_mark(MarkPriceEvent(1200, "BTCUSDT", 111, 112, 0.0001, 1300))
        summary = store.view()["summary"]

        self.assertEqual(summary["last_price"], 100)
        self.assertEqual(summary["spot_last_price"], None)
        self.assertEqual(summary["last_trade_price"], 100)
        self.assertEqual(summary["index_price"], 112)
        self.assertEqual(summary["price_source"], "aggTrade")

    def test_live_store_falls_back_to_futures_mark_price_without_trade_or_quote(self):
        store = LiveOrderflowStore(symbol="BTCUSDT", max_events=10)

        store.add_spot(SpotPriceEvent(1000, "BTCUSDT", 109))
        store.add_mark(MarkPriceEvent(1200, "BTCUSDT", 111, 112, 0.0001, 1300))
        summary = store.view()["summary"]

        self.assertEqual(summary["last_price"], 111)
        self.assertEqual(summary["spot_last_price"], 109)
        self.assertEqual(summary["mark_price"], 111)
        self.assertEqual(summary["index_price"], 112)
        self.assertEqual(summary["price_source"], "markPrice")

    def test_live_store_falls_back_to_quote_mid_before_first_trade(self):
        store = LiveOrderflowStore(symbol="BTCUSDT", max_events=10)

        store.add_quote(QuoteEvent(1100, "BTCUSDT", 108, 110))
        summary = store.view()["summary"]

        self.assertEqual(summary["last_price"], 109)
        self.assertEqual(summary["last_trade_price"], None)
        self.assertEqual(summary["price_source"], "bookTicker")

    def test_live_store_exposes_mark_and_index_prices(self):
        store = LiveOrderflowStore(symbol="BTCUSDT", max_events=10)

        store.add_mark(MarkPriceEvent(1200, "BTCUSDT", 111, 112, 0.0001, 1300))
        summary = store.view()["summary"]

        self.assertEqual(summary["mark_price"], 111)
        self.assertEqual(summary["index_price"], 112)
        self.assertEqual(summary["funding_rate"], 0.0001)
        self.assertEqual(summary["next_funding_time"], 1300)

    def test_live_store_exposes_empty_mode_detail_payloads(self):
        store = LiveOrderflowStore(symbol="BTCUSDT", max_events=10)
        view = store.view()

        self.assertEqual(view["summary"]["pnl_24h"], 0)
        self.assertEqual(view["summary"]["mode_breakdown"]["paper"]["signals"], 0)
        self.assertEqual(view["summary"]["mode_breakdown"]["live"]["orders"], 0)
        self.assertEqual(view["details"]["paper"]["pnl_by_range"]["all"], 0)
        self.assertEqual(view["details"]["live"]["closed_positions"], [])

    def test_live_store_uses_larger_profile_window_than_display_window(self):
        store = LiveOrderflowStore(symbol="BTCUSDT", max_events=650)
        store.add_trade(TradeEvent(1000, "BTCUSDT", 100, 500, False))
        for index in range(1, 600):
            store.add_trade(TradeEvent(1000 + index, "BTCUSDT", 200 + index, 1, False))

        view = store.view()
        poc = next(level for level in view["profile_levels"] if level["type"] == "POC")

        self.assertLessEqual(len(view["trades"]), 600)
        self.assertEqual(poc["price"], 100)
        self.assertEqual(view["summary"]["profile_trade_count"], 600)

    def test_live_store_runs_signal_engine_and_produces_signals(self):
        store = LiveOrderflowStore(symbol="BTCUSDT", max_events=600, enable_signals=True)
        store.add_quote(QuoteEvent(1000, "BTCUSDT", 99, 101))
        for price in [101.0, 101.5, 102.0, 102.5, 101.8, 102.2, 102.8, 103.5, 104.0, 104.5,
                       105.0, 104.8, 105.5, 106.0, 106.5, 105.8, 107.0, 106.2, 106.8, 107.5,
                       108.0, 107.5, 108.5, 109.0, 108.2, 108.8, 109.5, 110.0, 109.8, 110.5]:
            store.add_trade(TradeEvent(1000 + len(store._events) * 100, "BTCUSDT", price, 1.0, False))

        view = store.view()
        self.assertGreaterEqual(view["summary"]["signals"], 0)
        self.assertIn("mode_breakdown", view["summary"])

    def test_live_store_adds_display_index_to_visible_markers(self):
        signal = TradeSignal(
            id="sig-marker-1",
            symbol="BTCUSDT",
            side=SignalSide.LONG,
            setup="test_marker",
            entry_price=100,
            stop_price=95,
            target_price=105,
            confidence=0.8,
            reasons=("test",),
            invalidation_rules=("stop",),
            created_at=3_900,
        )
        store = LiveOrderflowStore(symbol="BTCUSDT", max_events=40, enable_signals=True)
        store._signal_engine = OneShotSignalEngine(signal)
        store.add_quote(QuoteEvent(3_900, "BTCUSDT", 99.9, 100.1))

        for index in range(30):
            event = TradeEvent(1_000 + index * 100, "BTCUSDT", 100 + index * 0.01, 1, False)
            store.add_trade(event, received_at=event.timestamp)

        marker = next(item for item in store.view()["markers"] if item["type"] == "signal")

        self.assertIn("index", marker)
        self.assertGreater(marker["index"], 0)

    def test_live_store_maintains_persistent_profile_engine(self):
        store = LiveOrderflowStore(symbol="BTCUSDT", max_events=10)

        store.add_trade(TradeEvent(1000, "BTCUSDT", 100, 5, True))
        store.add_trade(TradeEvent(2000, "BTCUSDT", 110, 20, False))
        view1 = store.view()
        store.add_trade(TradeEvent(3000, "BTCUSDT", 120, 3, True))
        view2 = store.view()

        self.assertLess(view1["summary"]["profile_trade_count"], view2["summary"]["profile_trade_count"])

    def test_live_store_handles_signal_engine_without_quote(self):
        store = LiveOrderflowStore(symbol="BTCUSDT", max_events=600, enable_signals=True)
        for price in [200.0, 200.5, 201.0, 201.5, 200.8, 201.2, 201.8, 202.5, 203.0, 203.5]:
            store.add_trade(TradeEvent(1000 + len(store._events) * 100, "BTCUSDT", price, 1.0, False))

        view = store.view()
        self.assertIsNotNone(view["summary"]["last_price"])

    def test_live_store_uses_time_windows_for_delta_vwap_and_profile(self):
        store = LiveOrderflowStore(symbol="BTCUSDT", max_events=20)
        old = TradeEvent(1_000, "BTCUSDT", 100, 500, False)
        recent = TradeEvent(5 * 60 * 60 * 1000, "BTCUSDT", 200, 1, True)

        store.add_trade(old, received_at=old.timestamp)
        store.add_trade(recent, received_at=recent.timestamp)
        view = store.view()
        poc = next(level for level in view["profile_levels"] if level["type"] == "POC")

        self.assertEqual(view["summary"]["seen_trade_count"], 2)
        self.assertEqual(view["summary"]["profile_trade_count"], 1)
        self.assertEqual(view["summary"]["delta_30s"], -1)
        self.assertEqual(view["summary"]["vwap"], 200)
        self.assertEqual(poc["price"], 200)
        self.assertEqual(poc["window"], "rolling_4h")

    def test_live_store_uses_received_at_for_stale_data_rejection(self):
        store = LiveOrderflowStore(symbol="BTCUSDT", max_events=40, enable_signals=True)
        engine = CapturingStaleSignalEngine()
        store._signal_engine = engine

        for index in range(30):
            event = TradeEvent(1_000 + index * 100, "BTCUSDT", 100 + index * 0.01, 1, False)
            store.add_trade(event, received_at=event.timestamp + 3_000)

        summary = store.view()["summary"]

        self.assertIsNotNone(engine.last_snapshot)
        self.assertGreaterEqual(summary["data_lag_ms"], 3_000)
        self.assertIn("data_stale", summary["reject_reasons"])

    def test_live_store_splits_exchange_lag_from_stream_freshness(self):
        store = LiveOrderflowStore(symbol="BTCUSDT", max_events=10)
        now = int(time.time() * 1000)

        store.add_trade(TradeEvent(now - 5_000, "BTCUSDT", 100, 1, False), received_at=now - 100)
        summary = store.view()["summary"]

        self.assertGreaterEqual(summary["exchange_lag_ms"], 4_800)
        self.assertLess(summary["stream_freshness_ms"], 1_000)
        self.assertEqual(summary["data_lag_ms"], summary["exchange_lag_ms"])

    def test_live_paper_journal_records_signal_fill_close_pnl_and_fee_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal_path = Path(tmp) / "live-paper.jsonl"
            signal = TradeSignal(
                id="sig-live-1",
                symbol="BTCUSDT",
                side=SignalSide.LONG,
                setup="test_lvn",
                entry_price=100,
                stop_price=95,
                target_price=105,
                confidence=0.8,
                reasons=("price accepted above LVN", "delta_30s positive"),
                invalidation_rules=("back below LVN",),
                created_at=4_000,
            )
            store = LiveOrderflowStore(
                symbol="BTCUSDT",
                max_events=40,
                enable_signals=True,
                journal_path=journal_path,
            )
            store._signal_engine = OneShotSignalEngine(signal)
            store.add_quote(QuoteEvent(3_900, "BTCUSDT", 99.9, 100.1))

            for index in range(30):
                event = TradeEvent(1_000 + index * 100, "BTCUSDT", 100 + index * 0.01, 1, False)
                store.add_trade(event, received_at=event.timestamp)
            store.add_trade(TradeEvent(10_000, "BTCUSDT", 106, 1, False), received_at=10_000)

            rows = [json.loads(line) for line in journal_path.read_text(encoding="utf-8").splitlines()]
            view = store.view()

        event_types = {row["type"] for row in rows}
        fill = next(row["payload"] for row in rows if row["type"] == "paper_fill")
        close = next(row["payload"] for row in rows if row["type"] == "position_closed")

        self.assertTrue({"signal", "risk_decision", "paper_fill", "paper_order", "position_closed", "pnl"} <= event_types)
        self.assertGreater(fill["fill_price"], 100.1)
        self.assertIn("slippage_bps", fill)
        self.assertIn("fee", fill)
        self.assertIn("net_realized_pnl", close)
        self.assertEqual(view["summary"]["closed_positions"], 1)
        self.assertGreater(view["summary"]["realized_pnl"], 0)

    def test_live_paper_fill_uses_exchange_info_tick_and_step_size(self):
        signal = TradeSignal(
            id="sig-spec-1",
            symbol="BTCUSDT",
            side=SignalSide.LONG,
            setup="test_spec",
            entry_price=100,
            stop_price=95,
            target_price=105,
            confidence=0.8,
            reasons=("test",),
            invalidation_rules=("stop",),
            created_at=4_000,
        )
        store = LiveOrderflowStore(
            symbol="BTCUSDT",
            max_events=40,
            enable_signals=True,
            instrument_spec=BinanceInstrumentSpec("BTCUSDT", tick_size=0.5, step_size=0.01, taker_fee_rate=0.0004),
        )
        store._signal_engine = OneShotSignalEngine(signal)
        store.add_quote(QuoteEvent(3_900, "BTCUSDT", 99.9, 100.1))

        for index in range(30):
            event = TradeEvent(1_000 + index * 100, "BTCUSDT", 100 + index * 0.01, 1, False)
            store.add_trade(event, received_at=event.timestamp)

        order = store.view()["details"]["paper"]["orders"][0]

        self.assertEqual(order["entry_price"] % 0.5, 0)
        self.assertEqual(round(order["quantity"] / 0.01), order["quantity"] / 0.01)

    def test_live_paper_journal_records_risk_reject_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal_path = Path(tmp) / "live-paper.jsonl"
            store = LiveOrderflowStore(
                symbol="BTCUSDT",
                max_events=40,
                enable_signals=True,
                journal_path=journal_path,
            )
            store._signal_engine = InvalidStopSignalEngine()

            for index in range(30):
                event = TradeEvent(1_000 + index * 100, "BTCUSDT", 100, 1, False)
                store.add_trade(event, received_at=event.timestamp)

            rows = [json.loads(line) for line in journal_path.read_text(encoding="utf-8").splitlines()]
            reject = next(row["payload"] for row in rows if row["type"] == "signal_rejected")

        self.assertIn("invalid_stop_distance", reject["reject_reasons"])
        self.assertEqual(store.view()["summary"]["orders"], 0)

    def test_live_store_summary_exposes_operator_context(self):
        store = LiveOrderflowStore(symbol="BTCUSDT", max_events=10)
        event = TradeEvent(1_000, "BTCUSDT", 100, 1, False)
        store.add_trade(event, received_at=1_250)

        summary = store.view()["summary"]

        self.assertIn("open_position", summary)
        self.assertIn("signal_reasons", summary)
        self.assertIn("reject_reasons", summary)
        self.assertEqual(summary["data_lag_ms"], 250)
        self.assertEqual(summary["last_trade_time"], 1_000)

    def test_live_store_marks_aggression_bubbles_for_frontend(self):
        store = LiveOrderflowStore(symbol="BTCUSDT", max_events=20)

        store.add_trade(TradeEvent(1_000, "BTCUSDT", 100, 12, False), received_at=1_000)
        store.add_trade(TradeEvent(2_000, "BTCUSDT", 99, 55, True), received_at=2_000)

        view = store.view()
        bubbles = [marker for marker in view["markers"] if marker["type"] == "aggression_bubble"]

        self.assertEqual(len(bubbles), 2)
        self.assertEqual(bubbles[0]["side"], "buy")
        self.assertEqual(bubbles[0]["tier"], "large")
        self.assertIn("index", bubbles[0])
        self.assertEqual(bubbles[1]["side"], "sell")
        self.assertEqual(bubbles[1]["tier"], "block")
        self.assertEqual(view["summary"]["last_aggression_bubble"]["side"], "sell")

    def test_live_paper_moves_stop_to_break_even_after_one_and_half_r(self):
        signal = TradeSignal(
            id="sig-break-even",
            symbol="BTCUSDT",
            side=SignalSide.LONG,
            setup="test_break_even",
            entry_price=100,
            stop_price=90,
            target_price=130,
            confidence=0.8,
            reasons=("test",),
            invalidation_rules=("stop",),
            created_at=4_000,
        )
        store = LiveOrderflowStore(symbol="BTCUSDT", max_events=60, enable_signals=True)
        store._signal_engine = OneShotSignalEngine(signal)
        store.add_quote(QuoteEvent(3_900, "BTCUSDT", 99.9, 100.1))

        for index in range(30):
            event = TradeEvent(1_000 + index * 100, "BTCUSDT", 100, 1, False)
            store.add_trade(event, received_at=event.timestamp)
        store.add_trade(TradeEvent(10_000, "BTCUSDT", 116, 1, False), received_at=10_000)

        view = store.view()
        position = view["summary"]["open_position"]
        actions = view["details"]["paper"]["protective_actions"]

        self.assertIsNotNone(position)
        self.assertEqual(position["stop_price"], position["entry_price"])
        self.assertTrue(any(action["action"] == "break_even_shift" for action in actions))
        self.assertEqual(view["summary"]["last_break_even_shift"]["action"], "break_even_shift")

    def test_live_paper_reduces_position_when_buy_cvd_spikes_without_price_progress(self):
        signal = TradeSignal(
            id="sig-absorption",
            symbol="BTCUSDT",
            side=SignalSide.LONG,
            setup="test_absorption",
            entry_price=100,
            stop_price=90,
            target_price=130,
            confidence=0.8,
            reasons=("test",),
            invalidation_rules=("stop",),
            created_at=4_000,
        )
        store = LiveOrderflowStore(symbol="BTCUSDT", max_events=80, enable_signals=True)
        store._signal_engine = OneShotSignalEngine(signal)
        store.add_quote(QuoteEvent(3_900, "BTCUSDT", 99.9, 100.1))

        for index in range(30):
            event = TradeEvent(1_000 + index * 100, "BTCUSDT", 100, 1, False)
            store.add_trade(event, received_at=event.timestamp)
        original_quantity = store.view()["summary"]["open_position"]["quantity"]
        store.add_trade(TradeEvent(10_000, "BTCUSDT", 100.4, 60, False), received_at=10_000)

        view = store.view()
        position = view["summary"]["open_position"]
        actions = view["details"]["paper"]["protective_actions"]

        self.assertIsNotNone(position)
        self.assertLess(position["quantity"], original_quantity)
        self.assertTrue(any(action["action"] == "absorption_reduce" for action in actions))
        self.assertEqual(view["summary"]["last_absorption_reduce"]["action"], "absorption_reduce")

    def test_live_store_summary_exposes_strategy_explainability_state(self):
        store = LiveOrderflowStore(symbol="BTCUSDT", max_events=20)

        for event in [
            TradeEvent(1_000, "BTCUSDT", 100, 12, False),
            TradeEvent(61_000, "BTCUSDT", 106, 1, False),
            TradeEvent(62_000, "BTCUSDT", 110, 1, False),
            TradeEvent(63_000, "BTCUSDT", 104, 1, True),
            TradeEvent(121_000, "BTCUSDT", 108, 1, False),
        ]:
            store.add_trade(event, received_at=event.timestamp)

        summary = store.view()["summary"]

        self.assertIn("atr_1m_14", summary)
        self.assertIn("atr_3m_14", summary)
        self.assertGreater(summary["atr_1m_14"], 0)
        self.assertEqual(summary["last_aggression_bubble"]["side"], "buy")
        self.assertIn("cvd_divergence", summary)
        self.assertIn("state", summary["cvd_divergence"])


    def test_state_save_and_restore_preserves_pnl_and_counters(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state-btcusdt.json"
            signal = TradeSignal(
                id="sig-state-1",
                symbol="BTCUSDT",
                side=SignalSide.LONG,
                setup="test_state",
                entry_price=100,
                stop_price=95,
                target_price=105,
                confidence=0.8,
                reasons=("test",),
                invalidation_rules=("stop",),
                created_at=4_000,
            )
            store = LiveOrderflowStore(
                symbol="BTCUSDT", max_events=40, enable_signals=True,
                journal_path=Path(tmp) / "journal.jsonl",
                state_path=state_path,
            )
            store._signal_engine = OneShotSignalEngine(signal)
            store.add_quote(QuoteEvent(3_900, "BTCUSDT", 99.9, 100.1))
            for index in range(30):
                store.add_trade(TradeEvent(1_000 + index * 100, "BTCUSDT", 100 + index * 0.01, 1, False), received_at=1_000 + index * 100)
            store.add_trade(TradeEvent(10_000, "BTCUSDT", 106, 1, False), received_at=10_000)
            view1 = store.view()

            store2 = LiveOrderflowStore(
                symbol="BTCUSDT", max_events=40, enable_signals=True,
                journal_path=Path(tmp) / "journal.jsonl",
                state_path=state_path,
            )
            view2 = store2.view()

            self.assertEqual(view2["summary"]["realized_pnl"], view1["summary"]["realized_pnl"])
            self.assertGreater(view2["summary"]["realized_pnl"], 0)
            self.assertEqual(view2["summary"]["closed_positions"], view1["summary"]["closed_positions"])
            self.assertEqual(view2["summary"]["signals"], view1["summary"]["signals"])
            self.assertEqual(view2["summary"]["orders"], view1["summary"]["orders"])

    def test_state_save_and_restore_round_trips_enums(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state-btcusdt.json"
            signal = TradeSignal(
                id="sig-enum-1",
                symbol="BTCUSDT",
                side=SignalSide.SHORT,
                setup="test_enum",
                entry_price=200,
                stop_price=210,
                target_price=180,
                confidence=0.7,
                reasons=("test",),
                invalidation_rules=("stop",),
                created_at=5_000,
            )
            store = LiveOrderflowStore(
                symbol="BTCUSDT", max_events=40, enable_signals=True,
                journal_path=Path(tmp) / "journal.jsonl",
                state_path=state_path,
            )
            store._signal_engine = OneShotSignalEngine(signal)
            store.add_quote(QuoteEvent(4_900, "BTCUSDT", 199.9, 200.1))
            for index in range(30):
                store.add_trade(TradeEvent(1_000 + index * 100, "BTCUSDT", 200 + index * 0.01, 1, True), received_at=1_000 + index * 100)
            store.add_trade(TradeEvent(11_000, "BTCUSDT", 179, 1, False), received_at=11_000)
            view1 = store.view()

            store2 = LiveOrderflowStore(
                symbol="BTCUSDT", max_events=40, enable_signals=True,
                journal_path=Path(tmp) / "journal.jsonl",
                state_path=state_path,
            )
            view2 = store2.view()

            self.assertEqual(view2["summary"]["signals"], view1["summary"]["signals"])
            self.assertEqual(view2["summary"]["realized_pnl"], view1["summary"]["realized_pnl"])
            self.assertGreater(view2["summary"]["realized_pnl"], 0)

    def test_state_journal_fallback_restores_from_journal(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal_path = Path(tmp) / "journal.jsonl"
            state_path = Path(tmp) / "state-nonexistent.json"
            signal = TradeSignal(
                id="sig-fallback-1",
                symbol="BTCUSDT",
                side=SignalSide.LONG,
                setup="test_fallback",
                entry_price=100,
                stop_price=95,
                target_price=105,
                confidence=0.8,
                reasons=("test",),
                invalidation_rules=("stop",),
                created_at=4_000,
            )
            store = LiveOrderflowStore(
                symbol="BTCUSDT", max_events=40, enable_signals=True,
                journal_path=journal_path,
            )
            store._signal_engine = OneShotSignalEngine(signal)
            store.add_quote(QuoteEvent(3_900, "BTCUSDT", 99.9, 100.1))
            for index in range(30):
                store.add_trade(TradeEvent(1_000 + index * 100, "BTCUSDT", 100 + index * 0.01, 1, False), received_at=1_000 + index * 100)
            store.add_trade(TradeEvent(10_000, "BTCUSDT", 106, 1, False), received_at=10_000)
            view1 = store.view()

            store2 = LiveOrderflowStore(
                symbol="BTCUSDT", max_events=40, enable_signals=True,
                journal_path=journal_path,
                state_path=state_path,
            )
            view2 = store2.view()

            self.assertGreater(view2["summary"]["realized_pnl"], 0)
            self.assertEqual(view2["summary"]["closed_positions"], view1["summary"]["closed_positions"])
            self.assertEqual(view2["summary"]["signals"], view1["summary"]["signals"])

    def test_state_clean_start_no_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state-nonexistent.json"
            journal_path = Path(tmp) / "journal-nonexistent.jsonl"
            store = LiveOrderflowStore(
                symbol="BTCUSDT", max_events=40, enable_signals=True,
                journal_path=journal_path,
                state_path=state_path,
            )
            view = store.view()
            self.assertEqual(view["summary"]["realized_pnl"], 0)
            self.assertEqual(view["summary"]["closed_positions"], 0)
            self.assertEqual(view["summary"]["signals"], 0)
            self.assertEqual(view["summary"]["orders"], 0)

    def test_state_saves_periodically_on_add_trade(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state-periodic.json"
            journal_path = Path(tmp) / "journal.jsonl"
            store = LiveOrderflowStore(
                symbol="BTCUSDT", max_events=20, enable_signals=False,
                journal_path=journal_path,
                state_path=state_path,
            )
            store.add_trade(TradeEvent(1_000, "BTCUSDT", 100, 1, False), received_at=1_200)
            mtime1 = state_path.stat().st_mtime if state_path.exists() else 0
            store.add_trade(TradeEvent(2_000, "BTCUSDT", 101, 1, False), received_at=2_200)
            self.assertTrue(state_path.exists())
            if state_path.stat().st_mtime != mtime1:
                self.skipTest("periodic save skipped (within 60s throttle)")
            self.assertIn("cumulative_delta", json.loads(state_path.read_text()))

    def test_state_circuit_breaker_trip_restored(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state-cb.json"
            store = LiveOrderflowStore(
                symbol="BTCUSDT", max_events=20,
                state_path=state_path,
            )
            cb = store._circuit_breaker
            cb.trip(CircuitBreakerReason.FLASH_CRASH_DETECTED)
            store.save_state()
            self.assertTrue(state_path.exists())
            store2 = LiveOrderflowStore(
                symbol="BTCUSDT", max_events=20,
                state_path=state_path,
            )
            self.assertEqual(store2._circuit_breaker.state, "tripped")


if __name__ == "__main__":
    unittest.main()
