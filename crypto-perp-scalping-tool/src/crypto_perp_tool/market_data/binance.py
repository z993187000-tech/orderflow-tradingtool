from __future__ import annotations

import asyncio
import json
import threading
import time
from dataclasses import dataclass
from typing import Callable

from crypto_perp_tool.market_data.events import MarkPriceEvent, QuoteEvent, TradeEvent


@dataclass(frozen=True)
class BinanceStreamConfig:
    symbol: str
    market_base_url: str = "wss://fstream.binance.com/market/stream"
    public_base_url: str = "wss://fstream.binance.com/public/stream"

    @property
    def market_streams(self) -> tuple[str, str]:
        symbol = self.symbol.lower()
        return (f"{symbol}@aggTrade", f"{symbol}@markPrice@1s")

    @property
    def public_streams(self) -> tuple[str, ...]:
        return (f"{self.symbol.lower()}@bookTicker",)

    @property
    def market_url(self) -> str:
        return f"{self.market_base_url}?streams={'/'.join(self.market_streams)}"

    @property
    def public_url(self) -> str:
        return f"{self.public_base_url}?streams={'/'.join(self.public_streams)}"


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


class BinanceMarkPriceParser:
    def parse(self, payload: dict) -> MarkPriceEvent:
        return MarkPriceEvent(
            timestamp=int(payload["E"]),
            symbol=str(payload["s"]).upper(),
            mark_price=float(payload["p"]),
            index_price=float(payload["i"]),
            funding_rate=float(payload["r"]),
            next_funding_time=int(payload["T"]),
        )


class BinanceAggTradeClient:
    def __init__(
        self,
        symbol: str,
        on_trade: Callable[[TradeEvent], None],
        on_quote: Callable[[QuoteEvent], None] | None = None,
        on_mark: Callable[[MarkPriceEvent], None] | None = None,
        on_status: Callable[[str, str], None] | None = None,
        reconnect_delay_seconds: float = 3.0,
    ) -> None:
        self.config = BinanceStreamConfig(symbol=symbol)
        self.on_trade = on_trade
        self.on_quote = on_quote
        self.on_mark = on_mark
        self.on_status = on_status
        self.reconnect_delay_seconds = reconnect_delay_seconds
        self.parser = BinanceAggTradeParser()
        self.quote_parser = BinanceBookTickerParser()
        self.mark_parser = BinanceMarkPriceParser()
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

        await asyncio.gather(
            self._consume_stream(self.config.market_url, "market", self.config.market_streams),
            self._consume_stream(self.config.public_url, "public", self.config.public_streams),
        )

    async def _consume_stream(self, url: str, route: str, streams: tuple[str, ...]) -> None:
        import websockets

        self._report_status("connecting", f"{route}: {url}")
        async with websockets.connect(url, ping_interval=20, ping_timeout=20) as websocket:
            self._report_status("connected", f"{route}: {'/'.join(streams)}")
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
            return
        if event_type == "markPriceUpdate" or "@markPrice" in stream:
            if self.on_mark is not None:
                self.on_mark(self.mark_parser.parse(data))

    def _report_status(self, status: str, message: str) -> None:
        if self.on_status is not None:
            self.on_status(status, message)
