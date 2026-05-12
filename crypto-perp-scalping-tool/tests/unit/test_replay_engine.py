import json
import tempfile
import unittest
from pathlib import Path

from crypto_perp_tool.replay.engine import ReplayEngine, ReplayReport


class ReplayEngineTests(unittest.TestCase):
    def _write_journal(self, path: Path, signals: list[dict]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            for sig in signals:
                event = {
                    "type": "signal",
                    "time": sig.get("created_at", 1000000),
                    "payload": {"signal": sig},
                }
                f.write(json.dumps(event, ensure_ascii=False) + "\n")

    def test_loads_journal_signals(self):
        journal_signals = [
            {
                "id": "sig-1", "symbol": "BTCUSDT", "side": "long",
                "setup": "lvn_break_acceptance", "entry_price": 96000,
                "stop_price": 95800, "target_price": 96500,
                "confidence": 0.8, "reasons": ["lvn accepted"],
                "invalidation_rules": [], "created_at": 1000000,
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            journal_path = Path(tmp) / "journal.jsonl"
            self._write_journal(journal_path, journal_signals)

            engine = ReplayEngine(journal_path, symbol="BTCUSDT")
            engine.load_journal()
            report = engine.replay([])

        self.assertEqual(report.total_journal_signals, 1)
        self.assertEqual(report.symbol, "BTCUSDT")

    def test_replay_with_no_events_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal_path = Path(tmp) / "empty.jsonl"
            journal_path.parent.mkdir(parents=True, exist_ok=True)
            journal_path.write_text("", encoding="utf-8")

            engine = ReplayEngine(journal_path, symbol="BTCUSDT")
            report = engine.replay([])

        self.assertEqual(report.total_journal_signals, 0)
        self.assertEqual(report.replayed_signals, 0)

    def test_replay_respects_time_range(self):
        journal_signals = [
            {
                "id": "sig-1", "symbol": "BTCUSDT", "side": "long",
                "setup": "lvn_break_acceptance", "entry_price": 96000,
                "stop_price": 95800, "target_price": 96500,
                "confidence": 0.8, "reasons": ["lvn accepted"],
                "invalidation_rules": [], "created_at": 1000000,
            },
            {
                "id": "sig-2", "symbol": "BTCUSDT", "side": "short",
                "setup": "lvn_breakdown_acceptance", "entry_price": 95000,
                "stop_price": 95200, "target_price": 94500,
                "confidence": 0.7, "reasons": ["lvn breakdown"],
                "invalidation_rules": [], "created_at": 2000000,
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            journal_path = Path(tmp) / "journal.jsonl"
            self._write_journal(journal_path, journal_signals)

            engine = ReplayEngine(journal_path, symbol="BTCUSDT")
            engine.load_journal(start_ms=500000, end_ms=1500000)
            report = engine.replay([])

        self.assertEqual(report.total_journal_signals, 1)

    def test_replay_report_structure(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal_path = Path(tmp) / "journal.jsonl"
            journal_path.parent.mkdir(parents=True, exist_ok=True)
            journal_path.write_text("", encoding="utf-8")

            engine = ReplayEngine(journal_path, symbol="ETHUSDT")
            report = engine.replay([])

        self.assertIsInstance(report, ReplayReport)
        self.assertEqual(report.symbol, "ETHUSDT")
        self.assertEqual(report.total_journal_signals, 0)
        self.assertEqual(report.matched, 0)
        self.assertEqual(len(report.matches), 0)

    def test_pct_diff_zero_for_equal_values(self):
        self.assertEqual(ReplayEngine._pct_diff(100.0, 100.0), 0.0)

    def test_pct_diff_zero_for_both_zero(self):
        self.assertEqual(ReplayEngine._pct_diff(0.0, 0.0), 0.0)

    def test_pct_diff_computes_correctly(self):
        diff = ReplayEngine._pct_diff(100.0, 101.0)
        self.assertAlmostEqual(diff, 0.9901, places=4)

    def test_pct_diff_handles_negative_values(self):
        diff = ReplayEngine._pct_diff(-100.0, -110.0)
        self.assertAlmostEqual(diff, 9.0909, places=4)

    def test_replay_match_includes_price_fields(self):
        from crypto_perp_tool.replay.engine import ReplayMatch
        match = ReplayMatch(
            original_time=1000000,
            original_setup="lvn_break_acceptance",
            original_side="long",
            replayed=True,
            replayed_entry_price=96000.0,
            original_entry_price=96100.0,
            entry_price_diff_pct=0.1042,
            replayed_stop_price=95800.0,
            original_stop_price=95850.0,
            stop_price_diff_pct=0.0522,
            replayed_target_price=96500.0,
            original_target_price=96400.0,
            target_price_diff_pct=0.1037,
            matched_prices=True,
        )
        self.assertTrue(match.matched_prices)
        self.assertEqual(match.replayed_entry_price, 96000.0)
        self.assertAlmostEqual(match.entry_price_diff_pct, 0.1042, places=4)

    def test_replay_report_has_price_stats(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal_path = Path(tmp) / "journal.jsonl"
            journal_path.parent.mkdir(parents=True, exist_ok=True)
            journal_path.write_text("", encoding="utf-8")

            engine = ReplayEngine(journal_path, symbol="BTCUSDT")
            report = engine.replay([])

        self.assertEqual(report.price_matched, 0)
        self.assertEqual(report.avg_entry_diff_pct, 0.0)
        self.assertEqual(report.avg_stop_diff_pct, 0.0)
        self.assertEqual(report.avg_target_diff_pct, 0.0)


if __name__ == "__main__":
    unittest.main()
