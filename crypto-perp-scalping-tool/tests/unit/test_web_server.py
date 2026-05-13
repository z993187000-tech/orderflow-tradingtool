import json
import tempfile
import unittest
from pathlib import Path

from crypto_perp_tool.market_data import KlineEvent
from crypto_perp_tool.web.server import create_app_handler, paper_journal_path_for_symbol, seed_historical_klines
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

    def test_symbol_specific_paper_journal_paths_do_not_collide(self):
        with tempfile.TemporaryDirectory() as tmp:
            base_path = Path(tmp) / "live-paper.jsonl"
            btc_path = paper_journal_path_for_symbol(base_path, "BTCUSDT")
            eth_path = paper_journal_path_for_symbol(base_path, "ETHUSDT")

        self.assertEqual(btc_path.name, "live-paper-btcusdt.jsonl")
        self.assertEqual(eth_path.name, "live-paper-ethusdt.jsonl")
        self.assertNotEqual(btc_path, eth_path)

    def test_seed_historical_klines_loads_recent_8h_5m_history(self):
        class FakeKlineClient:
            def __init__(self):
                self.calls = []

            def download(self, symbol, interval, limit, start_time=None, end_time=None):
                self.calls.append({
                    "symbol": symbol,
                    "interval": interval,
                    "limit": limit,
                    "start_time": start_time,
                    "end_time": end_time,
                })
                return [
                    KlineEvent(1_000, 300_999, symbol, interval, 100, 110, 90, 105, 1, 100, 2, True),
                    KlineEvent(301_000, 600_999, symbol, interval, 105, 115, 95, 111, 2, 200, 3, True),
                ]

        store = LiveOrderflowStore("BTCUSDT")
        client = FakeKlineClient()

        count = seed_historical_klines(store, client, now_ms=29_000_000)

        self.assertEqual(count, 2)
        self.assertEqual(client.calls, [{
            "symbol": "BTCUSDT",
            "interval": "5m",
            "limit": 96,
            "start_time": 200_000,
            "end_time": 29_000_000,
        }])
        self.assertEqual(len(store.view()["klines"]), 2)


if __name__ == "__main__":
    unittest.main()
