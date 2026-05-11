import json
import tempfile
import unittest
from pathlib import Path

from crypto_perp_tool.web.details import build_paper_details_from_journal


class WebDetailsTests(unittest.TestCase):
    def test_build_paper_details_reads_protective_actions_and_position_reductions(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal_path = Path(tmp) / "journal.jsonl"
            rows = [
                {
                    "type": "break_even_shift",
                    "time": 1_000,
                    "payload": {"timestamp": 900, "action": "break_even_shift", "signal_id": "sig-1", "stop_price": 100.0},
                },
                {
                    "type": "absorption_reduce",
                    "time": 2_000,
                    "payload": {
                        "timestamp": 1_900,
                        "action": "absorption_reduce",
                        "signal_id": "sig-1",
                        "quantity": 0.01,
                    },
                },
                {
                    "type": "position_reduced",
                    "time": 2_100,
                    "payload": {
                        "timestamp": 2_050,
                        "symbol": "BTCUSDT",
                        "side": "long",
                        "quantity": 0.01,
                        "entry_price": 100.0,
                        "close_price": 101.0,
                        "realized_pnl": 0.01,
                        "exit_reason": "absorption_reduce",
                    },
                },
            ]
            journal_path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")

            details = build_paper_details_from_journal(journal_path)

        actions = details["paper"]["protective_actions"]
        self.assertEqual([action["action"] for action in actions], ["break_even_shift", "absorption_reduce"])
        self.assertEqual(actions[0]["timestamp"], 900)
        self.assertEqual(actions[1]["quantity"], 0.01)
        self.assertEqual(details["paper"]["closed_positions"][0]["exit_reason"], "absorption_reduce")
        self.assertEqual(details["paper"]["pnl_by_range"]["all"], 0.01)


if __name__ == "__main__":
    unittest.main()
