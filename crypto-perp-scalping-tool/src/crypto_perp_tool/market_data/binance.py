from __future__ import annotations

import asyncio
import json
import threading
import time
from dataclasses import dataclass
from typing import Callable

from crypto_perp_tool.market_data.events import QuoteEvent, TradeEvent


@dataclass(frozen=True)
class BinanceStreamConfig:
    symbol: str
    base_url: str = "wss://fstream.binance.com/stream"

    @property
    def streams(self) -> tuple[str, str]:
        symbol = self.symbol.lower()
        return (f"{symbol}@aggTrade", f"{symbol}@bookTicker")

    @property
    def url(self) -> str:
        return f"{self.base_url}?streams={'/'.join(self.streams)}"


class BinanceAggTradeParser:
    def parse(self, payload: dict) -> TradeEvent:
        return TradeEvent(
            timestamp=int(payload.get("T") or payload["E"]),
            symbol=str(payload["s"]).upper(),
            price=float(payload["p"]),
            quantity=float(payload["q"]),
            is_buyer_maker=bool(payload["m"]),
        )


class BinanceBookTickerParser:
    def parse(self, payload: dict) -> QuoteEvent:
        return QuoteEvent(
            timestamp=int(payload.get("E") or payload.get("T") or 0),
            symbol=str(payload["s"]).upper(),
            bid_price=float(payload["b"]),
            ask_price=float(payload["a"]),
        )


class BinanceAggTradeClient:
    def __init__(
        self,
        symbol: str,
        on_trade: Callable[[TradeEvent], None],
        on_quote: Callable[[QuoteEvent], None] | None = None,
        on_status: Callable[[str, str], None] | None = None,
        reconnect_delay_seconds: float = 3.0,
    ) -> None:
        self.config = BinanceStreamConfig(symbol=symbol)
        self.on_trade = on_trade
        self.on_quote = on_quote
        self.on_status = on_status
        self.reconnect_delay_seconds = reconnect_delay_seconds
        self.parser = BinanceAggTradeParser()
        self.quote_parser = BinanceBookTickerParser()
        self._stop = threading.Event()

    def start_background(self) -> threading.Thread:
        thread = threading.Thread(target=self.run_forever, name=f"binance-{self.config.symbol.lower()}", daemon=True)
        thread.start()
        return thread

    def stop(self) -> None:
        self._stop.set()

    def run_forever(self) -> None:
        while not self._stop.is_set():
            try:
                asyncio.run(self._run_once())
            except Exception as exc:
                if self._stop.is_set():
                    break
                self._report_status("error", str(exc))
                time.sleep(self.reconnect_delay_seconds)

    async def _run_once(self) -> None:
        try:
            import websockets
        except ImportError as exc:
            raise RuntimeError("Install the 'websockets' package to use Binance live mode.") from exc

        self._report_status("connecting", self.config.url)
        async with websockets.connect(self.config.url, ping_interval=20, ping_timeout=20) as websocket:
            self._report_status("connected", "/".join(self.config.streams))
            while not self._stop.is_set():
                message = await websocket.recv()
                payload = json.loads(message)
                self._handle_payload(payload)

    def _handle_payload(self, payload: dict) -> None:
        stream = str(payload.get("stream") or "")
        data = payload.get("data") if "data" in payload else payload
        event_type = data.get("e")
        if event_type == "aggTrade" or stream.endswith("@aggTrade"):
            self.on_trade(self.parser.parse(data))
            return
        if event_type == "bookTicker" or stream.endswith("@bookTicker"):
            if self.on_quote is not None:
                self.on_quote(self.quote_parser.parse(data))

    def _report_status(self, status: str, message: str) -> None:
        if self.on_status is not None:
            self.on_status(status, message)
