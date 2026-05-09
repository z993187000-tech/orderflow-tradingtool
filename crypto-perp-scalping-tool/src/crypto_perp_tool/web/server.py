from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from crypto_perp_tool.market_data.binance import BinanceAggTradeClient
from crypto_perp_tool.web.auth import is_authorized, required_auth_header
from crypto_perp_tool.web.health import health_payload
from crypto_perp_tool.web.live_store import LiveOrderflowStore
from crypto_perp_tool.web.network import dashboard_urls
from crypto_perp_tool.web.orderflow import build_orderflow_view


STATIC_DIR = Path(__file__).with_name("static")


def create_app_handler(
    data_path: Path | str,
    journal_path: Path | str | None = None,
    live_store: LiveOrderflowStore | None = None,
    live_stores: dict[str, LiveOrderflowStore] | None = None,
    source: str = "csv",
    symbol: str = "BTCUSDT",
    password: str | None = None,
):
    data_path = Path(data_path)
    password = os.getenv("PASSWORD") if password is None else password

    class DashboardHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/healthz":
                self._send_json(health_payload(source=source, symbol=symbol))
                return
            if not is_authorized(self.headers, password):
                self._send_unauthorized()
                return
            if parsed.path == "/api/orderflow":
                query = parse_qs(parsed.query)
                requested_symbol = query.get("symbol", [symbol])[0].upper()
                if live_stores is not None:
                    store = live_stores.get(requested_symbol) or live_stores[symbol.upper()]
                    payload = store.view()
                elif live_store is not None:
                    payload = live_store.view()
                else:
                    payload = build_orderflow_view(data_path, symbol=requested_symbol)
                self._send_json(payload)
                return

            static_path = "index.html" if parsed.path in ("/", "/index.html") else parsed.path.lstrip("/")
            file_path = (STATIC_DIR / static_path).resolve()
            if STATIC_DIR.resolve() not in file_path.parents and file_path != STATIC_DIR.resolve():
                self.send_error(403)
                return
            if not file_path.exists() or not file_path.is_file():
                self.send_error(404)
                return
            self._send_file(file_path)

        def log_message(self, format: str, *args) -> None:
            return

        def _send_json(self, payload: dict) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_unauthorized(self) -> None:
            body = b"Authentication required"
            self.send_response(401)
            self.send_header("WWW-Authenticate", required_auth_header())
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_file(self, file_path: Path) -> None:
            body = file_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", _content_type(file_path))
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return DashboardHandler


def serve_dashboard(
    host: str,
    port: int,
    data_path: Path | str,
    source: str = "csv",
    symbol: str = "BTCUSDT",
    paper_journal_path: Path | str | None = None,
) -> ThreadingHTTPServer:
    live_stores = None
    clients = []
    if source == "binance":
        symbols = tuple(dict.fromkeys([symbol.upper(), "BTCUSDT", "ETHUSDT"]))
        live_stores = {}
        base_journal_path = Path(paper_journal_path) if paper_journal_path is not None else None
        for live_symbol in symbols:
            symbol_journal_path = (
                paper_journal_path_for_symbol(base_journal_path, live_symbol) if base_journal_path is not None else None
            )
            store = LiveOrderflowStore(symbol=live_symbol, paper_journal_path=symbol_journal_path)
            live_stores[live_symbol] = store
            client = BinanceAggTradeClient(
                symbol=live_symbol,
                on_trade=store.add_trade,
                on_quote=store.add_quote,
                on_mark=store.add_mark,
                on_spot=store.add_spot,
                on_status=store.set_connection_status,
            )
            client.start_background()
            clients.append(client)
    handler = create_app_handler(data_path=data_path, live_stores=live_stores, source=source, symbol=symbol)
    server = ThreadingHTTPServer((host, port), handler)
    urls = dashboard_urls(host, port)
    print(f"Order-flow dashboard source={source} symbol={symbol}")
    print(f"Local: {urls['local']}")
    for url in urls["lan"]:
        print(f"Phone/LAN: {url}")
    try:
        server.serve_forever()
    finally:
        for client in clients:
            client.stop()
    return server


def paper_journal_path_for_symbol(base_path: Path | str, symbol: str) -> Path:
    base_path = Path(base_path)
    suffix = base_path.suffix or ".jsonl"
    stem = base_path.stem if base_path.suffix else base_path.name
    return base_path.with_name(f"{stem}-{symbol.lower()}{suffix}")


def _content_type(path: Path) -> str:
    if path.suffix == ".html":
        return "text/html; charset=utf-8"
    if path.suffix == ".css":
        return "text/css; charset=utf-8"
    if path.suffix == ".js":
        return "application/javascript; charset=utf-8"
    return "application/octet-stream"
