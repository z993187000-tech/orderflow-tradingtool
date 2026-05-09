from __future__ import annotations

import threading
from collections import deque
from typing import Any

from crypto_perp_tool.config import default_settings
from crypto_perp_tool.market_data import QuoteEvent, TradeEvent
from crypto_perp_tool.profile import VolumeProfileEngine
from crypto_perp_tool.web.details import empty_execution_details, mode_breakdown, total_pnl_for_range


class LiveOrderflowStore:
    def __init__(self, symbol: str, max_events: int = 20_000, display_events: int = 500) -> None:
        self.symbol = symbol.upper()
        self.max_events = max_events
        self.display_events = display_events
        self._events: deque[TradeEvent] = deque(maxlen=max_events)
        self._quote: QuoteEvent | None = None
        self._connection_status = "starting"
        self._connection_message = "waiting for Binance stream"
        self._lock = threading.Lock()

    def add_trade(self, event: TradeEvent) -> None:
        if event.symbol.upper() != self.symbol:
            return
        with self._lock:
            self._events.append(event)

    def add_quote(self, event: QuoteEvent) -> None:
        if event.symbol.upper() != self.symbol:
            return
        with self._lock:
            self._quote = event

    def set_connection_status(self, status: str, message: str) -> None:
        with self._lock:
            self._connection_status = status
            self._connection_message = message

    def view(self) -> dict[str, Any]:
        with self._lock:
            events = list(self._events)
            quote = self._quote
            connection_status = self._connection_status
            connection_message = self._connection_message

        settings = default_settings()
        bin_size = settings.profile.btc_bin_size if self.symbol == "BTCUSDT" else settings.profile.eth_bin_size
        profile = VolumeProfileEngine(bin_size=bin_size, value_area_ratio=settings.profile.value_area_ratio)
        cumulative_delta = 0.0
        trades: list[dict[str, Any]] = []
        delta_series: list[dict[str, Any]] = []
        display_events = events[-self.display_events :]
        details = empty_execution_details()

        for event in events:
            profile.add_trade(event.price, event.quantity)

        for index, event in enumerate(display_events):
            cumulative_delta += event.delta
            trades.append(
                {
                    "index": index,
                    "timestamp": event.timestamp,
                    "symbol": event.symbol,
                    "price": event.price,
                    "quantity": event.quantity,
                    "side": "sell" if event.is_buyer_maker else "buy",
                    "delta": event.delta,
                }
            )
            delta_series.append(
                {
                    "index": index,
                    "timestamp": event.timestamp,
                    "delta": event.delta,
                    "cumulative_delta": cumulative_delta,
                }
            )

        fallback_trade_price = trades[-1]["price"] if trades else None
        last_price = quote.mid_price if quote is not None else fallback_trade_price
        return {
            "summary": {
                "source": "binance",
                "symbol": self.symbol,
                "connection_status": connection_status,
                "connection_message": connection_message,
                "trade_count": len(trades),
                "profile_trade_count": len(events),
                "last_price": last_price,
                "bid_price": quote.bid_price if quote is not None else None,
                "ask_price": quote.ask_price if quote is not None else None,
                "price_source": "bookTicker" if quote is not None else "aggTrade",
                "cumulative_delta": cumulative_delta,
                "signals": 0,
                "orders": 0,
                "closed_positions": 0,
                "realized_pnl": 0,
                "pnl_24h": total_pnl_for_range(details, "24h"),
                "mode_breakdown": mode_breakdown(details),
            },
            "trades": trades,
            "delta_series": delta_series,
            "profile_levels": [
                {
                    "type": level.type.value,
                    "price": level.price,
                    "lower_bound": level.lower_bound,
                    "upper_bound": level.upper_bound,
                    "strength": level.strength,
                    "window": level.window,
                }
                for level in profile.levels("rolling_4h")
            ],
            "markers": [],
            "details": details,
        }
