import json
import tempfile
import unittest
from pathlib import Path

from crypto_perp_tool.web.server import create_app_handler
from crypto_perp_tool.web.live_store import LiveOrderflowStore


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class WebServerTests(unittest.TestCase):
    def test_static_assets_exist(self):
        static_dir = PROJECT_ROOT / "src" / "crypto_perp_tool" / "web" / "static"

        self.assertTrue((static_dir / "index.html").exists())
        self.assertTrue((static_dir / "app.css").exists())
        self.assertTrue((static_dir / "app.js").exists())

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


if __name__ == "__main__":
    unittest.main()
