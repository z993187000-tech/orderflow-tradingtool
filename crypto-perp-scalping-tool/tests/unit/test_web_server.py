import json
import threading
import tempfile
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path

from crypto_perp_tool.market_data import KlineEvent
from crypto_perp_tool.web.server import active_live_symbols, create_app_handler, paper_journal_path_for_symbol, seed_historical_klines
from crypto_perp_tool.web.live_store import LiveOrderflowStore


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class WebServerTests(unittest.TestCase):
    def post_backtest(self, payload: dict) -> tuple[int, dict]:
        handler = create_app_handler(
            data_path=PROJECT_ROOT / "data" / "sample_trades.csv",
            source="csv",
            symbol="BTCUSDT",
            password="",
        )
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        connection = None
        try:
            connection = HTTPConnection("127.0.0.1", server.server_port, timeout=10)
            body = json.dumps(payload).encode("utf-8")
            connection.request(
                "POST",
                "/api/backtest/run",
                body=body,
                headers={"Content-Type": "application/json", "Content-Length": str(len(body))},
            )
            response = connection.getresponse()
            response_body = response.read().decode("utf-8")
            try:
                payload = json.loads(response_body)
            except json.JSONDecodeError:
                payload = {"raw": response_body}
            return response.status, payload
        finally:
            if connection is not None:
                connection.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

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

    def test_backtest_run_endpoint_returns_report(self):
        status, payload = self.post_backtest({
            "csv_path": "data/btcusdt_recent.csv",
            "symbol": "BTCUSDT",
            "equity": 10_000,
        })

        self.assertEqual(status, 200)
        self.assertEqual(payload["mode"], "single")
        self.assertEqual(payload["symbol"], "BTCUSDT")
        self.assertGreater(payload["total_events"], 0)
        self.assertIn("report", payload)
        self.assertIn("equity_curve", payload)

    def test_backtest_run_endpoint_rejects_unsafe_paths(self):
        status, payload = self.post_backtest({
            "csv_path": "../CLAUDE.md",
            "symbol": "BTCUSDT",
            "equity": 10_000,
        })

        self.assertEqual(status, 400)
        self.assertIn("error", payload)

    def test_symbol_specific_paper_journal_paths_do_not_collide(self):
        with tempfile.TemporaryDirectory() as tmp:
            base_path = Path(tmp) / "live-paper.jsonl"
            btc_path = paper_journal_path_for_symbol(base_path, "BTCUSDT")
            eth_path = paper_journal_path_for_symbol(base_path, "ETHUSDT")

        self.assertEqual(btc_path.name, "live-paper-btcusdt.jsonl")
        self.assertEqual(eth_path.name, "live-paper-ethusdt.jsonl")
        self.assertNotEqual(btc_path, eth_path)

    def test_active_live_symbols_defaults_to_requested_symbol_only(self):
        self.assertEqual(active_live_symbols("BTCUSDT"), ("BTCUSDT",))

    def test_active_live_symbols_can_enable_multiple_symbols_explicitly(self):
        symbols = active_live_symbols("BTCUSDT", "ethusdt, BTCUSDT, ethusdt")

        self.assertEqual(symbols, ("BTCUSDT", "ETHUSDT"))

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

        self.assertEqual(count, 6)
        self.assertEqual(client.calls, [
            {"symbol": "BTCUSDT", "interval": "1m", "limit": 20, "start_time": 200_000, "end_time": 29_000_000},
            {"symbol": "BTCUSDT", "interval": "3m", "limit": 20, "start_time": 200_000, "end_time": 29_000_000},
            {"symbol": "BTCUSDT", "interval": "5m", "limit": 96, "start_time": 200_000, "end_time": 29_000_000},
        ])
        self.assertEqual(len(store.view()["klines"]), 6)


if __name__ == "__main__":
    unittest.main()
