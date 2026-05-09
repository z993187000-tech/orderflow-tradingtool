import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def cli_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = "src"
    return env


class CliTests(unittest.TestCase):
    def test_config_show_outputs_json(self):
        result = subprocess.run(
            [sys.executable, "-m", "crypto_perp_tool.cli", "config", "show"],
            cwd=PROJECT_ROOT,
            env=cli_env(),
            text=True,
            capture_output=True,
            check=True,
        )

        payload = json.loads(result.stdout)

        self.assertEqual(payload["mode"], "paper")
        self.assertEqual(payload["symbols"], ["BTCUSDT", "ETHUSDT"])

    def test_web_serve_help_mentions_binance_source(self):
        result = subprocess.run(
            [sys.executable, "-m", "crypto_perp_tool.cli", "web", "serve", "--help"],
            cwd=PROJECT_ROOT,
            env=cli_env(),
            text=True,
            capture_output=True,
            check=True,
        )

        self.assertIn("--source", result.stdout)
        self.assertIn("binance", result.stdout)
        self.assertIn("--mobile", result.stdout)

    def test_paper_run_outputs_summary_and_writes_journal(self):
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
            csv_path = Path(tmp) / "trades.csv"
            journal_path = Path(tmp) / "journal.jsonl"
            csv_path.write_text(csv_text, encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "crypto_perp_tool.cli",
                    "paper",
                    "run",
                    "--csv",
                    str(csv_path),
                    "--journal",
                    str(journal_path),
                ],
                cwd=PROJECT_ROOT,
                env=cli_env(),
                text=True,
                capture_output=True,
                check=True,
            )
            payload = json.loads(result.stdout)

            self.assertGreaterEqual(payload["signals"], 1)
            self.assertGreaterEqual(payload["closed_positions"], 1)
            self.assertTrue(journal_path.exists())

    def test_journal_tail_outputs_recent_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal_path = Path(tmp) / "journal.jsonl"
            journal_path.write_text(
                '{"type":"one","time":1,"payload":{}}\n{"type":"two","time":2,"payload":{"ok":true}}\n',
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "crypto_perp_tool.cli",
                    "journal",
                    "tail",
                    "--path",
                    str(journal_path),
                    "--limit",
                    "1",
                ],
                cwd=PROJECT_ROOT,
                env=cli_env(),
                text=True,
                capture_output=True,
                check=True,
            )
            payload = json.loads(result.stdout)

        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["type"], "two")

    def test_risk_check_outputs_decision_from_json(self):
        signal = {
            "id": "sig-cli",
            "symbol": "BTCUSDT",
            "side": "long",
            "setup": "lvn_break_acceptance",
            "entry_price": 100,
            "stop_price": 99,
            "target_price": 102,
            "confidence": 0.7,
            "reasons": ["accepted above LVN"],
            "invalidation_rules": ["back below LVN"],
            "created_at": 1,
        }
        account = {"equity": 10000, "realized_pnl_today": 0, "consecutive_losses": 0}

        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "risk.json"
            input_path.write_text(json.dumps({"signal": signal, "account": account}), encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "crypto_perp_tool.cli",
                    "risk",
                    "check",
                    "--json",
                    str(input_path),
                ],
                cwd=PROJECT_ROOT,
                env=cli_env(),
                text=True,
                capture_output=True,
                check=True,
            )
            payload = json.loads(result.stdout)

        self.assertTrue(payload["allowed"])
        self.assertEqual(payload["quantity"], 25.0)


if __name__ == "__main__":
    unittest.main()
