from __future__ import annotations

import asyncio
import json
import threading
import time
from dataclasses import dataclass
from typing import Callable
from urllib.request import urlopen

from crypto_perp_tool.market_data.events import MarkPriceEvent, QuoteEvent, SpotPriceEvent, TradeEvent


_INSTRUMENT_SPEC_CACHE: dict[str, "BinanceInstrumentSpec"] = {}


@dataclass(frozen=True)
class BinanceInstrumentSpec:
    symbol: str
    tick_size: float
    step_size: float
    taker_fee_rate: float = 0.0004


@dataclass(frozen=True)
class BinanceStreamConfig:
    symbol: str
    market_base_url: str = "wss://fstream.binance.com/market/stream"
    public_base_url: str = "wss://fstream.binance.com/public/stream"
    spot_base_url: str = "wss://stream.binance.com:9443/stream"

    @property
    def market_streams(self) -> tuple[str, str]:
        symbol = self.symbol.lower()
        return (f"{symbol}@aggTrade", f"{symbol}@markPrice@1s")

    @property
    def public_streams(self) -> tuple[str, ...]:
        return (f"{self.symbol.lower()}@bookTicker",)

    @property
    def spot_streams(self) -> tuple[str, ...]:
        return (f"{self.symbol.lower()}@trade",)

    @property
    def market_url(self) -> str:
        return f"{self.market_base_url}?streams={'/'.join(self.market_streams)}"

    @property
    def public_url(self) -> str:
        return f"{self.public_base_url}?streams={'/'.join(self.public_streams)}"

    @property
    def spot_url(self) -> str:
        return f"{self.spot_base_url}?streams={'/'.join(self.spot_streams)}"


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


class BinanceSpotTradeParser:
    def parse(self, payload: dict) -> SpotPriceEvent:
        return SpotPriceEvent(
            timestamp=int(payload.get("T") or payload["E"]),
            symbol=str(payload["s"]).upper(),
            price=float(payload["p"]),
        )


class BinanceExchangeInfoParser:
    def parse_symbol(self, payload: dict, symbol: str) -> BinanceInstrumentSpec:
        symbol = symbol.upper()
        symbol_info = next((item for item in payload.get("symbols", []) if item.get("symbol") == symbol), None)
        if symbol_info is None:
            raise ValueError(f"symbol not found in exchangeInfo: {symbol}")

        filters = {item.get("filterType"): item for item in symbol_info.get("filters", [])}
        price_filter = filters.get("PRICE_FILTER")
        lot_size = filters.get("LOT_SIZE")
        if price_filter is None or lot_size is None:
            raise ValueError(f"missing PRICE_FILTER or LOT_SIZE for symbol: {symbol}")

        tick_size = float(price_filter["tickSize"])
        step_size = float(lot_size["stepSize"])
        if tick_size <= 0 or step_size <= 0:
            raise ValueError(f"invalid tickSize or stepSize for symbol: {symbol}")
        return BinanceInstrumentSpec(symbol=symbol, tick_size=tick_size, step_size=step_size)


class BinanceExchangeInfoClient:
    def __init__(
        self,
        url: str = "https://fapi.binance.com/fapi/v1/exchangeInfo",
        timeout_seconds: float = 5.0,
        loader: Callable[[str, float], dict] | None = None,
    ) -> None:
        self.url = url
        self.timeout_seconds = timeout_seconds
        self.loader = loader or self._load_json
        self.parser = BinanceExchangeInfoParser()

    def fetch_symbol(self, symbol: str) -> BinanceInstrumentSpec:
        payload = self.loader(self.url, self.timeout_seconds)
        return self.parser.parse_symbol(payload, symbol)

    def _load_json(self, url: str, timeout: float) -> dict:
        with urlopen(url, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))


def default_instrument_spec(symbol: str) -> BinanceInstrumentSpec:
    symbol = symbol.upper()
    if symbol == "BTCUSDT":
        return BinanceInstrumentSpec(symbol=symbol, tick_size=0.1, step_size=0.001)
    if symbol == "ETHUSDT":
        return BinanceInstrumentSpec(symbol=symbol, tick_size=0.01, step_size=0.001)
    return BinanceInstrumentSpec(symbol=symbol, tick_size=0.01, step_size=0.001)


def fetch_instrument_spec(symbol: str) -> BinanceInstrumentSpec:
    symbol = symbol.upper()
    if symbol in _INSTRUMENT_SPEC_CACHE:
        return _INSTRUMENT_SPEC_CACHE[symbol]
    try:
        spec = BinanceExchangeInfoClient().fetch_symbol(symbol)
    except Exception:
        spec = default_instrument_spec(symbol)
    _INSTRUMENT_SPEC_CACHE[symbol] = spec
    return spec


class BinanceAggTradeClient:
    def __init__(
        self,
        symbol: str,
        on_trade: Callable[[TradeEvent], None],
        on_quote: Callable[[QuoteEvent], None] | None = None,
        on_mark: Callable[[MarkPriceEvent], None] | None = None,
        on_spot: Callable[[SpotPriceEvent], None] | None = None,
        on_status: Callable[[str, str], None] | None = None,
        reconnect_delay_seconds: float = 3.0,
    ) -> None:
        self.config = BinanceStreamConfig(symbol=symbol)
        self.on_trade = on_trade
        self.on_quote = on_quote
        self.on_mark = on_mark
        self.on_spot = on_spot
        self.on_status = on_status
        self.reconnect_delay_seconds = reconnect_delay_seconds
        self.parser = BinanceAggTradeParser()
        self.quote_parser = BinanceBookTickerParser()
        self.mark_parser = BinanceMarkPriceParser()
        self.spot_parser = BinanceSpotTradeParser()
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
            self._consume_stream_forever(self.config.market_url, "market", self.config.market_streams),
            self._consume_stream_forever(self.config.public_url, "public", self.config.public_streams),
            self._consume_stream_forever(self.config.spot_url, "spot", self.config.spot_streams),
        )

    async def _consume_stream_forever(self, url: str, route: str, streams: tuple[str, ...]) -> None:
        while not self._stop.is_set():
            try:
                await self._consume_stream(url, route, streams)
            except Exception as exc:
                if self._stop.is_set():
                    break
                self._report_status("error", f"{route}: {exc}")
                await asyncio.sleep(self.reconnect_delay_seconds)

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
            return
        if event_type == "trade" or stream.endswith("@trade"):
            if self.on_spot is not None:
                self.on_spot(self.spot_parser.parse(data))

    def _report_status(self, status: str, message: str) -> None:
        if self.on_status is not None:
            self.on_status(status, message)
