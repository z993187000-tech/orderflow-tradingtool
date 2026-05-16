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
        self.assertIn("--paper-journal", result.stdout)
        self.assertIn("--symbols", result.stdout)

    def test_paper_run_outputs_summary_and_writes_journal(self):
        csv_text = "\n".join(
            [
                "timestamp,symbol,price,quantity,is_buyer_maker",
                "1000,BTCUSDT,96000,50,false",
                "2000,BTCUSDT,96020,60,false",
                "3000,BTCUSDT,96010,30,false",
                "4000,BTCUSDT,95990,20,false",
                "5000,BTCUSDT,96030,40,false",
                "6000,BTCUSDT,96015,35,false",
                "7000,BTCUSDT,95995,25,false",
                "8000,BTCUSDT,96025,45,false",
                "9000,BTCUSDT,96008,15,false",
                "10000,BTCUSDT,96035,55,false",
                "11000,BTCUSDT,96480,60,false",
                "12000,BTCUSDT,96510,70,false",
                "13000,BTCUSDT,96490,50,false",
                "14000,BTCUSDT,96520,65,false",
                "15000,BTCUSDT,96505,55,false",
                "16000,BTCUSDT,96530,50,false",
                "17000,BTCUSDT,96485,40,false",
                "18000,BTCUSDT,96515,60,false",
                "19000,BTCUSDT,96500,35,false",
                "20000,BTCUSDT,96525,45,false",
                "21000,BTCUSDT,96110,2,true",
                "22000,BTCUSDT,96100,1,true",
                "23000,BTCUSDT,96090,1,true",
                "24000,BTCUSDT,96105,1,true",
                "25000,BTCUSDT,96120,3,false",
                "26000,BTCUSDT,96130,4,false",
                "27000,BTCUSDT,96115,3,false",
                "28000,BTCUSDT,96140,5,false",
                "29000,BTCUSDT,96125,4,false",
                "30000,BTCUSDT,96150,6,false",
                "31000,BTCUSDT,96135,5,false",
                "32000,BTCUSDT,96160,7,false",
                "33000,BTCUSDT,96145,6,false",
                "34000,BTCUSDT,96170,8,false",
                "35000,BTCUSDT,96155,5,false",
                "36000,BTCUSDT,96180,9,false",
                "37000,BTCUSDT,96165,12,false",
                "38000,BTCUSDT,96195,10,false",
                "39000,BTCUSDT,96175,8,false",
                "40000,BTCUSDT,96200,15,false",
                "41000,BTCUSDT,96185,10,false",
                "42000,BTCUSDT,96220,14,false",
                "43000,BTCUSDT,96210,11,false",
                "44000,BTCUSDT,96250,12,false",
                "45000,BTCUSDT,96230,9,false",
                "46000,BTCUSDT,96280,16,false",
                "47000,BTCUSDT,96260,13,false",
                "48000,BTCUSDT,96300,18,false",
                "49000,BTCUSDT,96320,10,false",
                "50000,BTCUSDT,96350,15,false",
                "51000,BTCUSDT,96380,12,false",
                "52000,BTCUSDT,96400,20,false",
                "53000,BTCUSDT,96430,14,false",
                "54000,BTCUSDT,96460,18,false",
                "55000,BTCUSDT,96500,22,false",
                "56000,BTCUSDT,96450,15,false",
                "57000,BTCUSDT,96540,8,false",
                "58000,BTCUSDT,96510,12,false",
                "59000,BTCUSDT,96580,10,false",
                "60000,BTCUSDT,96550,6,false",
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
        account = {"equity": 10000}

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

    def test_simulation_run_outputs_fault_scenario_report(self):
        result = subprocess.run(
            [sys.executable, "-m", "crypto_perp_tool.cli", "simulation", "run"],
            cwd=PROJECT_ROOT,
            env=cli_env(),
            text=True,
            capture_output=True,
            check=True,
        )
        payload = json.loads(result.stdout)

        self.assertEqual(payload["scenarios"], 5)
        self.assertIn("websocket_disconnect", payload["by_scenario"])
        self.assertIn("stop_submission_failure", payload["by_scenario"])
        self.assertIn("protective_close", payload["by_scenario"]["stop_submission_failure"]["protective_actions"])


if __name__ == "__main__":
    unittest.main()
