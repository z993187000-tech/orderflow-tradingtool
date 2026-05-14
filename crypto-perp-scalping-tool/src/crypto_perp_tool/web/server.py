from __future__ import annotations

import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from crypto_perp_tool.journal import JsonlJournal
from crypto_perp_tool.market_data.binance import BinanceAggTradeClient, BinanceHistoricalKlineClient, fetch_instrument_spec
from crypto_perp_tool.service import TradingService
from crypto_perp_tool.telegram_bot import TelegramCommandHandler, TelegramPoller, parse_allowed_chat_ids
from crypto_perp_tool.web.auth import is_authorized, required_auth_header
from crypto_perp_tool.web.health import health_payload
from crypto_perp_tool.web.live_store import LiveOrderflowStore
from crypto_perp_tool.web.network import dashboard_urls
from crypto_perp_tool.web.orderflow import build_orderflow_view


STATIC_DIR = Path(__file__).with_name("static")
KLINE_HISTORY_LIMIT = 96
KLINE_INTERVAL = "5m"
KLINE_LOOKBACK_MS = 8 * 60 * 60 * 1000


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

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            if not is_authorized(self.headers, password):
                self._send_unauthorized()
                return
            if parsed.path == "/api/circuit/resume":
                if live_stores is not None:
                    store_symbol = parse_qs(parsed.query).get("symbol", [symbol])[0].upper()
                    store = live_stores.get(store_symbol)
                else:
                    store = live_store
                if store is None:
                    self._send_json({"resumed": False, "reason": "no live store"})
                    return
                result = store.resume_circuit(actor="web")
                self._send_json(result)
                return
            if parsed.path == "/api/trade-log":
                query = parse_qs(parsed.query)
                requested_symbol = query.get("symbol", [symbol])[0].upper()
                fmt = query.get("format", ["csv"])[0]
                if live_stores is not None:
                    store = live_stores.get(requested_symbol) or live_stores.get(symbol.upper())
                else:
                    store = live_store
                if store is None or store._trade_log is None:
                    self._send_json({"error": "no trade log available"})
                    return
                if fmt == "csv":
                    self._send_csv_trade_log(store._trade_log, requested_symbol)
                else:
                    records = store._trade_log.read_all()
                    self._send_json([r.to_csv_row() for r in records])
                return
            self.send_error(404)

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

        def _send_csv_trade_log(self, trade_log, symbol_name: str) -> None:
            import csv as csv_mod
            import io

            records = trade_log.read_all()
            headers = records[0].csv_headers() if records else []
            buf = io.StringIO()
            writer = csv_mod.DictWriter(buf, fieldnames=headers, extrasaction="ignore")
            writer.writeheader()
            for record in records:
                writer.writerow(record.to_csv_row())
            body = buf.getvalue().encode("utf-8-sig")
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="trade-log-{symbol_name.lower()}.csv"')
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


def seed_historical_klines(
    store: LiveOrderflowStore,
    client: BinanceHistoricalKlineClient | None = None,
    now_ms: int | None = None,
) -> int:
    client = client or BinanceHistoricalKlineClient()
    now_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
    start_ms = now_ms - KLINE_LOOKBACK_MS
    klines = client.download(
        store.symbol,
        interval=KLINE_INTERVAL,
        limit=KLINE_HISTORY_LIMIT,
        start_time=start_ms,
        end_time=now_ms,
    )
    store.seed_klines(klines)
    return len(klines)


def active_live_symbols(primary_symbol: str, symbols: str | tuple[str, ...] | list[str] | None = None) -> tuple[str, ...]:
    primary = primary_symbol.upper()
    raw_symbols: list[str] = []
    if symbols is None:
        raw_symbols = [primary]
    elif isinstance(symbols, str):
        raw_symbols = [item.strip().upper() for item in symbols.split(",") if item.strip()]
    else:
        raw_symbols = [str(item).strip().upper() for item in symbols if str(item).strip()]

    ordered: list[str] = []
    for item in [primary, *raw_symbols]:
        if item and item not in ordered:
            ordered.append(item)
    return tuple(ordered)


def serve_dashboard(
    host: str,
    port: int,
    data_path: Path | str,
    source: str = "csv",
    symbol: str = "BTCUSDT",
    symbols: str | tuple[str, ...] | list[str] | None = None,
    paper_journal_path: Path | str | None = None,
    testing_mode: bool = False,
) -> ThreadingHTTPServer:
    live_stores = None
    clients = []
    if source == "binance":
        symbols = active_live_symbols(symbol, symbols)
        live_stores = {}
        base_journal_path = Path(paper_journal_path) if paper_journal_path is not None else None
        for live_symbol in symbols:
            symbol_journal_path = (
                paper_journal_path_for_symbol(base_journal_path, live_symbol) if base_journal_path is not None else None
            )
            symbol_trade_log_path = (
                paper_journal_path_for_symbol(base_journal_path.with_name("trade-log"), live_symbol)
                if base_journal_path is not None else None
            )
            symbol_state_path = (
                base_journal_path.parent / f"state-{live_symbol.lower()}.json"
                if base_journal_path is not None else None
            )
            store = LiveOrderflowStore(
                symbol=live_symbol,
                enable_signals=True,
                journal_path=symbol_journal_path,
                trade_log_path=symbol_trade_log_path,
                instrument_spec=fetch_instrument_spec(live_symbol),
                testing_mode=testing_mode,
                state_path=symbol_state_path,
            )
            live_stores[live_symbol] = store
            try:
                seeded_count = seed_historical_klines(store)
                if seeded_count:
                    store.set_connection_status(
                        "starting",
                        f"seeded {seeded_count} historical 5m klines; waiting for Binance stream",
                    )
            except Exception as exc:
                store.set_connection_status(
                    "error",
                    f"historical 5m kline seed failed: {exc}; waiting for Binance stream",
                )
            client = BinanceAggTradeClient(
                symbol=live_symbol,
                on_trade=store.add_trade,
                on_quote=store.add_quote,
                on_mark=store.add_mark,
                on_spot=store.add_spot,
                on_force_order=store.add_force_order,
                on_kline=store.add_kline,
                on_status=store.set_connection_status,
            )
            client.start_background()
            clients.append(client)

    poller = None
    if live_stores is not None and paper_journal_path is not None:
        telegram_journal = JsonlJournal(
            Path(paper_journal_path).with_name(Path(paper_journal_path).stem + "-telegram" + Path(paper_journal_path).suffix)
        )
        service = TradingService(journal=telegram_journal)
        allowed_chat_ids = parse_allowed_chat_ids(os.environ.get("TELEGRAM_ALLOWED_CHAT_IDS", ""))
        primary_store = live_stores.get(symbol.upper(), next(iter(live_stores.values())))
        restore_info = getattr(primary_store, '_restored_state_info', {"paused": False})
        if restore_info.get("paused"):
            service.paused = True
        bot_handler = TelegramCommandHandler(service=service, allowed_chat_ids=allowed_chat_ids, store=primary_store)
        service.set_store(primary_store)
        poller = TelegramPoller(handler=bot_handler, journal=telegram_journal)
        poller.start()

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
        if live_stores is not None:
            paused = service.paused if 'service' in dir() else False
            for store in live_stores.values():
                try:
                    store.save_state(paused=paused)
                except Exception:
                    pass
        if poller is not None:
            poller.stop()
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
