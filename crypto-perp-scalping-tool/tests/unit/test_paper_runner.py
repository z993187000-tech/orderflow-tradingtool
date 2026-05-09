import tempfile
import unittest
from pathlib import Path

from crypto_perp_tool.paper import PaperRunner


class PaperRunnerTests(unittest.TestCase):
    def test_paper_runner_replays_csv_and_writes_journal(self):
        csv_text = "\n".join(
            [
                "timestamp,symbol,price,quantity,is_buyer_maker",
                "1000,BTCUSDT,100,5,true",
                "2000,BTCUSDT,110,20,false",
                "3000,BTCUSDT,120,3,true",
                "4000,BTCUSDT,130,5,true",
                "5000,BTCUSDT,140,30,false",
                "6000,BTCUSDT,150,5,true",
                "7000,BTCUSDT,126,12,false",
                "8000,BTCUSDT,141,10,false",
            ]
        )

        with tempfile.TemporaryDirectory() as tmp:
            data_path = Path(tmp) / "trades.csv"
            journal_path = Path(tmp) / "journal.jsonl"
            data_path.write_text(csv_text, encoding="utf-8")

            result = PaperRunner(equity=10_000, journal_path=journal_path).run_csv(data_path)
            journal_text = journal_path.read_text(encoding="utf-8")

        self.assertGreaterEqual(result.signals, 1)
        self.assertGreaterEqual(result.orders, 1)
        self.assertGreaterEqual(result.closed_positions, 1)
        self.assertGreater(result.realized_pnl, 0)
        self.assertIn("\"type\": \"signal\"", journal_text)
        self.assertIn("\"type\": \"risk_decision\"", journal_text)
        self.assertIn("\"type\": \"paper_fill\"", journal_text)
        self.assertIn("\"type\": \"position_closed\"", journal_text)

    def test_paper_runner_rejects_csv_missing_required_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_path = Path(tmp) / "bad.csv"
            data_path.write_text("timestamp,price\n1000,100\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "missing required columns"):
                PaperRunner(equity=10_000, journal_path=Path(tmp) / "journal.jsonl").run_csv(data_path)


if __name__ == "__main__":
    unittest.main()
