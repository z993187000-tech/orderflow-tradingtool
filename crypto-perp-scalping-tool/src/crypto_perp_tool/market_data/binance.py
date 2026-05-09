from __future__ import annotations

import asyncio
import json
import threading
import time
from dataclasses import dataclass
from typing import Callable

from crypto_perp_tool.market_data.events import TradeEvent


@dataclass(frozen=True)
class BinanceStreamConfig:
    symbol: str
    base_url: str = "wss://fstream.binance.com/market/ws"

    @property
    def stream_name(self) -> str:
        return f"{self.symbol.lower()}@aggTrade"

    @property
    def url(self) -> str:
        return f"{self.base_url}/{self.stream_name}"


class BinanceAggTradeParser:
    def parse(self, payload: dict) -> TradeEvent:
        return TradeEvent(
            timestamp=int(payload.get("T") or payload["E"]),
            symbol=str(payload["s"]).upper(),
            price=float(payload["p"]),
            quantity=float(payload["q"]),
            is_buyer_maker=bool(payload["m"]),
        )


class BinanceAggTradeClient:
    def __init__(
        self,
        symbol: str,
        on_trade: Callable[[TradeEvent], None],
        on_status: Callable[[str, str], None] | None = None,
        reconnect_delay_seconds: float = 3.0,
    ) -> None:
        self.config = BinanceStreamConfig(symbol=symbol)
        self.on_trade = on_trade
        self.on_status = on_status
        self.reconnect_delay_seconds = reconnect_delay_seconds
        self.parser = BinanceAggTradeParser()
        self._stop = threading.Event()

    def start_background(self) -> threading.Thread:
        thread = threading.Thread(target=self.run_forever, name=f"binance-{self.config.stream_name}", daemon=True)
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
            self._report_status("connected", self.config.stream_name)
            while not self._stop.is_set():
                message = await websocket.recv()
                payload = json.loads(message)
                self.on_trade(self.parser.parse(payload))

    def _report_status(self, status: str, message: str) -> None:
        if self.on_status is not None:
            self.on_status(status, message)
