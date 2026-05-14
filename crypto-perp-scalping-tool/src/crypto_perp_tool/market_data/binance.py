from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable
from urllib.request import Request, urlopen

from crypto_perp_tool.market_data.events import ForceOrderEvent, KlineEvent, MarkPriceEvent, QuoteEvent, SpotPriceEvent, TradeEvent


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
    def market_streams(self) -> tuple[str, ...]:
        symbol = self.symbol.lower()
        return (f"{symbol}@aggTrade", f"{symbol}@markPrice@1s", f"{symbol}@forceOrder", f"{symbol}@kline_5m")

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
            exchange_event_time=int(payload.get("E") or payload.get("T")),
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


class BinanceForceOrderParser:
    def parse(self, payload: dict) -> ForceOrderEvent:
        order = payload.get("o", payload)
        return ForceOrderEvent(
            timestamp=int(payload.get("T") or payload.get("E", 0)),
            symbol=str(order.get("s", "")).upper(),
            price=float(order.get("p", 0)),
            quantity=float(order.get("q", 0)),
            side=str(order.get("S", "")),
            order_type="LIQUIDATION",
        )


class BinanceSpotTradeParser:
    def parse(self, payload: dict) -> SpotPriceEvent:
        return SpotPriceEvent(
            timestamp=int(payload.get("T") or payload["E"]),
            symbol=str(payload["s"]).upper(),
            price=float(payload["p"]),
        )


class BinanceKlineParser:
    def parse(self, payload: dict) -> "KlineEvent":
        from crypto_perp_tool.market_data.events import KlineEvent

        k = payload["k"]
        return KlineEvent(
            timestamp=int(k["t"]),
            close_time=int(k["T"]),
            symbol=str(k["s"]).upper(),
            interval=str(k["i"]),
            open=float(k["o"]),
            high=float(k["h"]),
            low=float(k["l"]),
            close=float(k["c"]),
            volume=float(k["v"]),
            quote_volume=float(k["q"]),
            trade_count=int(k["n"]),
            is_closed=bool(k["x"]),
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
        on_force_order: Callable[[ForceOrderEvent], None] | None = None,
        on_kline: Callable[["KlineEvent"], None] | None = None,
        on_status: Callable[[str, str], None] | None = None,
        reconnect_delay_seconds: float = 3.0,
    ) -> None:
        self.config = BinanceStreamConfig(symbol=symbol)
        self.on_trade = on_trade
        self.on_quote = on_quote
        self.on_mark = on_mark
        self.on_spot = on_spot
        self.on_force_order = on_force_order
        self.on_kline = on_kline
        self.on_status = on_status
        self.reconnect_delay_seconds = reconnect_delay_seconds
        self.parser = BinanceAggTradeParser()
        self.quote_parser = BinanceBookTickerParser()
        self.mark_parser = BinanceMarkPriceParser()
        self.force_order_parser = BinanceForceOrderParser()
        self.spot_parser = BinanceSpotTradeParser()
        self.kline_parser = BinanceKlineParser()
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
        if event_type == "forceOrder" or stream.endswith("@forceOrder"):
            if self.on_force_order is not None:
                self.on_force_order(self.force_order_parser.parse(data))
            return
        if event_type == "trade" or stream.endswith("@trade"):
            if self.on_spot is not None:
                self.on_spot(self.spot_parser.parse(data))
            return
        if event_type == "kline" or stream.endswith("@kline_5m"):
            if self.on_kline is not None:
                self.on_kline(self.kline_parser.parse(data))

    def _report_status(self, status: str, message: str) -> None:
        if self.on_status is not None:
            self.on_status(status, message)


# ------------------------------------------------------------------
# Authenticated REST client for Binance Futures
# ------------------------------------------------------------------


class BinanceAuthenticatedClient:
    """Authenticated REST client for Binance Futures signed endpoints.

    Uses HMAC-SHA256 signing. Zero external dependencies — only stdlib urllib, hmac, hashlib.
    Only instantiated in live mode with explicit confirmation; paper mode returns None
    from the factory function.
    """

    MAX_RETRIES = 3
    RETRY_DELAY_SECONDS = 1.0

    def __init__(self, api_key: str, api_secret: str, base_url: str = "https://fapi.binance.com") -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url.rstrip("/")

    # ------------------------------------------------------------------
    # Public fetch methods
    # ------------------------------------------------------------------

    def fetch_positions(self, symbol: str | None = None) -> dict[str, dict[str, Any]]:
        """Fetch open positions from GET /fapi/v2/positionRisk.

        Returns dict keyed by uppercase symbol, each value with keys:
        quantity, entry_price, side (long/short), unrealized_pnl, leverage.
        Zero-quantity positions are filtered out.
        """
        params: dict[str, str] = {}
        if symbol:
            params["symbol"] = symbol.upper()
        response = self._signed_request("/fapi/v2/positionRisk", params)
        positions: dict[str, dict[str, Any]] = {}
        for item in response:
            qty = float(item.get("positionAmt", 0))
            if qty == 0:
                continue
            sym = str(item["symbol"]).upper()
            positions[sym] = {
                "quantity": abs(qty),
                "entry_price": float(item.get("entryPrice", 0)),
                "side": "long" if qty > 0 else "short",
                "unrealized_pnl": float(item.get("unRealizedProfit", 0)),
                "leverage": int(float(item.get("leverage", 1))),
            }
        return positions

    def fetch_open_orders(self, symbol: str | None = None) -> dict[str, list[dict[str, Any]]]:
        """Fetch open orders from GET /fapi/v1/openOrders.

        Returns dict keyed by uppercase symbol, each value a list of order dicts.
        """
        params: dict[str, str] = {}
        if symbol:
            params["symbol"] = symbol.upper()
        response = self._signed_request("/fapi/v1/openOrders", params)
        orders: dict[str, list[dict[str, Any]]] = {}
        for item in response:
            sym = str(item["symbol"]).upper()
            orders.setdefault(sym, []).append({
                "orderId": item.get("orderId"),
                "symbol": sym,
                "type": item.get("type", ""),
                "side": item.get("side", ""),
                "price": float(item.get("price", 0)),
                "stopPrice": float(item.get("stopPrice", 0)),
                "quantity": float(item.get("origQty", 0)),
                "reduceOnly": bool(item.get("reduceOnly", False)),
                "status": item.get("status", ""),
            })
        return orders

    # ------------------------------------------------------------------
    # Request signing
    # ------------------------------------------------------------------

    def _signed_request(self, endpoint: str, params: dict[str, str]) -> Any:
        params["timestamp"] = str(int(time.time() * 1000))
        params["recvWindow"] = str(5000)
        query = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        signature = hmac.new(
            self.api_secret.encode("utf-8"), query.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        url = f"{self.base_url}{endpoint}?{query}&signature={signature}"

        last_exc: Exception | None = None
        delay = self.RETRY_DELAY_SECONDS
        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                req = Request(url, headers={"X-MBX-APIKEY": self.api_key})
                with urlopen(req, timeout=10) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                if exc.code == 418:
                    raise RuntimeError("Binance HTTP 418: IP banned") from exc
                if exc.code == 429:
                    last_exc = exc
                    if attempt < self.MAX_RETRIES:
                        time.sleep(delay)
                        delay *= 2
                    continue
                raise RuntimeError(f"Binance HTTP {exc.code}: {exc.reason}") from exc
            except Exception as exc:
                last_exc = exc
                if attempt < self.MAX_RETRIES:
                    time.sleep(delay)
                    delay *= 2

        raise RuntimeError(f"Binance request failed after {self.MAX_RETRIES} retries: rate limit") from last_exc


def create_authenticated_client_if_live(settings: Any) -> BinanceAuthenticatedClient | None:
    """Factory that returns a BinanceAuthenticatedClient only when all safety gates pass.

    Returns None unless ALL of:
    - settings.mode == "live"
    - LIVE_TRADING_CONFIRMATION env var is set
    - BINANCE_FUTURES_API_KEY env var is set
    - BINANCE_FUTURES_API_SECRET env var is set
    """
    from crypto_perp_tool.config import Settings
    if not isinstance(settings, Settings):
        return None
    if settings.mode != "live":
        return None
    if os.getenv("LIVE_TRADING_CONFIRMATION") != "I_UNDERSTAND_LIVE_RISK":
        return None
    api_key = os.getenv("BINANCE_FUTURES_API_KEY", "")
    api_secret = os.getenv("BINANCE_FUTURES_API_SECRET", "")
    if not api_key or not api_secret:
        return None
    return BinanceAuthenticatedClient(api_key=api_key, api_secret=api_secret)


class BinanceHistoricalAggTradeClient:
    """Downloads historical aggTrades from the public REST endpoint.

    GET /fapi/v1/aggTrades — no API key required. Same urlopen/loader
    injection pattern as BinanceExchangeInfoClient.
    """

    BASE_URL = "https://fapi.binance.com"
    MAX_LIMIT = 1000

    def __init__(
        self,
        base_url: str | None = None,
        timeout_seconds: float = 10.0,
        loader: Callable[[str, float], list] | None = None,
    ) -> None:
        self.base_url = (base_url or self.BASE_URL).rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.loader = loader or self._load_json

    def _load_json(self, url: str, timeout: float) -> list:
        proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
        last_err = None
        for attempt in range(3):
            try:
                if proxy:
                    handler = urllib.request.ProxyHandler({"https": proxy, "http": proxy.replace("https", "http")})
                    opener = urllib.request.build_opener(handler)
                    req = Request(url, headers={"User-Agent": "crypto-perp-tool"})
                    with opener.open(req, timeout=timeout) as response:
                        return json.loads(response.read().decode("utf-8"))
                req = Request(url, headers={"User-Agent": "crypto-perp-tool"})
                with urlopen(req, timeout=timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
            except Exception as exc:
                last_err = exc
                if attempt < 2:
                    time.sleep(1.0 * (attempt + 1))
        raise last_err  # type: ignore[misc]

    def fetch(
        self, symbol: str, start_time: int | None = None,
        end_time: int | None = None, from_id: int | None = None,
        limit: int = MAX_LIMIT,
    ) -> list[dict]:
        """Fetch one page of raw aggTrade JSON dicts."""
        params = [f"symbol={symbol.upper()}", f"limit={min(limit, self.MAX_LIMIT)}"]
        if start_time is not None:
            params.append(f"startTime={start_time}")
        if end_time is not None:
            params.append(f"endTime={end_time}")
        if from_id is not None:
            params.append(f"fromId={from_id}")
        url = f"{self.base_url}/fapi/v1/aggTrades?{'&'.join(params)}"
        return self.loader(url, self.timeout_seconds)

    def download(
        self, symbol: str, start_time: int | None = None,
        end_time: int | None = None, max_pages: int = 200,
    ) -> list[TradeEvent]:
        """Paginate through historical aggTrades and return TradeEvents."""
        trades: list[TradeEvent] = []
        from_id: int | None = None
        sym = symbol.upper()

        for page in range(max_pages):
            # Only page 0 uses start_time; fromId+endTime together → HTTP 400
            st = start_time if page == 0 else None
            et = end_time if page == 0 else None
            batch = self.fetch(symbol=sym, start_time=st, end_time=et, from_id=from_id)
            if not batch:
                break

            for row in batch:
                ts = int(row["T"])
                if end_time and ts > end_time:
                    break
                trades.append(TradeEvent(
                    timestamp=ts, symbol=sym,
                    price=float(row["p"]), quantity=float(row["q"]),
                    is_buyer_maker=bool(row["m"]),
                ))

            if end_time and int(batch[-1]["T"]) > end_time:
                break
            from_id = int(batch[-1]["a"])
            time.sleep(0.15)

        return trades


class BinanceHistoricalKlineClient:
    """Downloads historical Futures klines from the public REST endpoint."""

    BASE_URL = "https://fapi.binance.com"
    MAX_LIMIT = 1500

    def __init__(
        self,
        base_url: str | None = None,
        timeout_seconds: float = 10.0,
        loader: Callable[[str, float], list] | None = None,
    ) -> None:
        self.base_url = (base_url or self.BASE_URL).rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.loader = loader or self._load_json

    def _load_json(self, url: str, timeout: float) -> list:
        proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
        last_err = None
        for attempt in range(3):
            try:
                if proxy:
                    handler = urllib.request.ProxyHandler({"https": proxy, "http": proxy.replace("https", "http")})
                    opener = urllib.request.build_opener(handler)
                    req = Request(url, headers={"User-Agent": "crypto-perp-tool"})
                    with opener.open(req, timeout=timeout) as response:
                        return json.loads(response.read().decode("utf-8"))
                req = Request(url, headers={"User-Agent": "crypto-perp-tool"})
                with urlopen(req, timeout=timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
            except Exception as exc:
                last_err = exc
                if attempt < 2:
                    time.sleep(1.0 * (attempt + 1))
        raise last_err  # type: ignore[misc]

    def fetch(
        self,
        symbol: str,
        interval: str = "5m",
        limit: int = 96,
        start_time: int | None = None,
        end_time: int | None = None,
    ) -> list:
        params = [f"symbol={symbol.upper()}", f"interval={interval}", f"limit={min(limit, self.MAX_LIMIT)}"]
        if start_time is not None:
            params.append(f"startTime={start_time}")
        if end_time is not None:
            params.append(f"endTime={end_time}")
        url = f"{self.base_url}/fapi/v1/klines?{'&'.join(params)}"
        return self.loader(url, self.timeout_seconds)

    def download(
        self,
        symbol: str,
        interval: str = "5m",
        limit: int = 96,
        start_time: int | None = None,
        end_time: int | None = None,
    ) -> list[KlineEvent]:
        sym = symbol.upper()
        rows = self.fetch(sym, interval=interval, limit=limit, start_time=start_time, end_time=end_time)
        return [self._parse_row(row, sym, interval) for row in rows]

    def _parse_row(self, row: list, symbol: str, interval: str) -> KlineEvent:
        return KlineEvent(
            timestamp=int(row[0]),
            close_time=int(row[6]),
            symbol=symbol,
            interval=interval,
            open=float(row[1]),
            high=float(row[2]),
            low=float(row[3]),
            close=float(row[4]),
            volume=float(row[5]),
            quote_volume=float(row[7]),
            trade_count=int(row[8]),
            is_closed=True,
        )
