import json
import tempfile
import unittest
from pathlib import Path

from crypto_perp_tool.web.server import create_app_handler, paper_journal_path_for_symbol
from crypto_perp_tool.web.live_store import LiveOrderflowStore


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class WebServerTests(unittest.TestCase):
    def test_static_assets_exist(self):
        static_dir = PROJECT_ROOT / "src" / "crypto_perp_tool" / "web" / "static"

        self.assertTrue((static_dir / "index.html").exists())
        self.assertTrue((static_dir / "app.css").exists())
        self.assertTrue((static_dir / "app.js").exists())

    def test_static_dashboard_text_is_not_mojibake(self):
        static_dir = PROJECT_ROOT / "src" / "crypto_perp_tool" / "web" / "static"
        app_text = (static_dir / "app.js").read_text(encoding="utf-8")

        self.assertNotIn("妯℃嫙", app_text)
        self.assertNotIn("瀹炵洏", app_text)
        self.assertNotIn("鏃堕棿", app_text)
        self.assertIn("模拟", app_text)
        self.assertIn("实盘", app_text)
        self.assertIn("时间", app_text)

    def test_handler_factory_accepts_data_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal_path = Path(tmp) / "web_journal.jsonl"
            handler = create_app_handler(
                data_path=PROJECT_ROOT / "data" / "sample_trades.csv",
                journal_path=journal_path,
            )

        self.assertTrue(callable(handler))

    def test_handler_factory_accepts_live_store(self):
        handler = create_app_handler(
            data_path=PROJECT_ROOT / "data" / "sample_trades.csv",
            live_store=LiveOrderflowStore("BTCUSDT"),
            source="binance",
            symbol="BTCUSDT",
        )

        self.assertTrue(callable(handler))

    def test_handler_factory_accepts_live_stores_for_multiple_symbols(self):
        handler = create_app_handler(
            data_path=PROJECT_ROOT / "data" / "sample_trades.csv",
            live_stores={
                "BTCUSDT": LiveOrderflowStore("BTCUSDT"),
                "ETHUSDT": LiveOrderflowStore("ETHUSDT"),
            },
            source="binance",
            symbol="BTCUSDT",
        )

        self.assertTrue(callable(handler))

    def test_symbol_specific_paper_journal_paths_do_not_collide(self):
        with tempfile.TemporaryDirectory() as tmp:
            base_path = Path(tmp) / "live-paper.jsonl"

            btc_path = paper_journal_path_for_symbol(base_path, "BTCUSDT")
            eth_path = paper_journal_path_for_symbol(base_path, "ETHUSDT")

        self.assertEqual(btc_path.name, "live-paper-btcusdt.jsonl")
        self.assertEqual(eth_path.name, "live-paper-ethusdt.jsonl")
        self.assertNotEqual(btc_path, eth_path)


if __name__ == "__main__":
    unittest.main()
