import json
import tempfile
import unittest
from pathlib import Path

from crypto_perp_tool.market_data import MarkPriceEvent, QuoteEvent, SpotPriceEvent, TradeEvent
from crypto_perp_tool.market_data.binance import BinanceInstrumentSpec
from crypto_perp_tool.types import SignalSide, TradeSignal
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
            TradeEvent(1000, "BTCUSDT", 100, 5, True),
            TradeEvent(2000, "BTCUSDT", 110, 20, False),
            TradeEvent(3000, "BTCUSDT", 120, 3, True),
            TradeEvent(4000, "BTCUSDT", 130, 5, True),
            TradeEvent(5000, "BTCUSDT", 140, 30, False),
            TradeEvent(6000, "BTCUSDT", 150, 5, True),
            TradeEvent(7000, "BTCUSDT", 126, 12, False),
        ]:
            store.add_trade(event)

        view = store.view()

        self.assertEqual(view["summary"]["source"], "binance")
        self.assertEqual(view["summary"]["symbol"], "BTCUSDT")
        self.assertEqual(view["summary"]["trade_count"], 7)
        self.assertEqual(view["summary"]["last_price"], 126)
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

    def test_live_store_uses_spot_trade_as_display_last_price(self):
        store = LiveOrderflowStore(symbol="BTCUSDT", max_events=10)

        store.add_trade(TradeEvent(1000, "BTCUSDT", 100, 1, False))
        store.add_quote(QuoteEvent(1100, "BTCUSDT", 108, 110))
        store.add_spot(SpotPriceEvent(1200, "BTCUSDT", 112))
        summary = store.view()["summary"]

        self.assertEqual(summary["last_price"], 112)
        self.assertEqual(summary["spot_last_price"], 112)
        self.assertEqual(summary["last_trade_price"], 100)
        self.assertEqual(summary["bid_price"], 108)
        self.assertEqual(summary["ask_price"], 110)
        self.assertEqual(summary["quote_mid_price"], 109)
        self.assertEqual(summary["price_source"], "spotTrade")

    def test_live_store_falls_back_to_perp_trade_before_first_spot_trade(self):
        store = LiveOrderflowStore(symbol="BTCUSDT", max_events=10)

        store.add_trade(TradeEvent(1000, "BTCUSDT", 100, 1, False))
        store.add_quote(QuoteEvent(1100, "BTCUSDT", 108, 110))
        summary = store.view()["summary"]

        self.assertEqual(summary["last_price"], 100)
        self.assertEqual(summary["spot_last_price"], None)
        self.assertEqual(summary["last_trade_price"], 100)
        self.assertEqual(summary["price_source"], "aggTrade")

    def test_live_store_falls_back_to_index_price_before_perp_trade(self):
        store = LiveOrderflowStore(symbol="BTCUSDT", max_events=10)

        store.add_trade(TradeEvent(1000, "BTCUSDT", 100, 1, False))
        store.add_mark(MarkPriceEvent(1200, "BTCUSDT", 111, 112, 0.0001, 1300))
        summary = store.view()["summary"]

        self.assertEqual(summary["last_price"], 112)
        self.assertEqual(summary["spot_last_price"], None)
        self.assertEqual(summary["last_trade_price"], 100)
        self.assertEqual(summary["index_price"], 112)
        self.assertEqual(summary["price_source"], "indexPrice")

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

        self.assertLessEqual(len(view["trades"]), 500)
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

    def test_live_store_uses_received_at_for_stale_data_rejection(self):
        store = LiveOrderflowStore(symbol="BTCUSDT", max_events=40, enable_signals=True)
        engine = CapturingStaleSignalEngine()
        store._signal_engine = engine

        for index in range(30):
            event = TradeEvent(1_000 + index * 100, "BTCUSDT", 100 + index, 1, False)
            store.add_trade(event, received_at=event.timestamp + 3_000)

        summary = store.view()["summary"]

        self.assertIsNotNone(engine.last_snapshot)
        self.assertGreaterEqual(summary["data_lag_ms"], 3_000)
        self.assertIn("data_stale", summary["reject_reasons"])

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


if __name__ == "__main__":
    unittest.main()
