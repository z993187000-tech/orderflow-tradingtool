from __future__ import annotations

import threading
from collections import deque
from typing import Any

from crypto_perp_tool.config import default_settings
from crypto_perp_tool.market_data import TradeEvent
from crypto_perp_tool.profile import VolumeProfileEngine


class LiveOrderflowStore:
    def __init__(self, symbol: str, max_events: int = 500) -> None:
        self.symbol = symbol.upper()
        self.max_events = max_events
        self._events: deque[TradeEvent] = deque(maxlen=max_events)
        self._connection_status = "starting"
        self._connection_message = "waiting for Binance stream"
        self._lock = threading.Lock()

    def add_trade(self, event: TradeEvent) -> None:
        if event.symbol.upper() != self.symbol:
            return
        with self._lock:
            self._events.append(event)

    def set_connection_status(self, status: str, message: str) -> None:
        with self._lock:
            self._connection_status = status
            self._connection_message = message

    def view(self) -> dict[str, Any]:
        with self._lock:
            events = list(self._events)
            connection_status = self._connection_status
            connection_message = self._connection_message

        settings = default_settings()
        bin_size = settings.profile.btc_bin_size if self.symbol == "BTCUSDT" else settings.profile.eth_bin_size
        profile = VolumeProfileEngine(bin_size=bin_size, value_area_ratio=settings.profile.value_area_ratio)
        cumulative_delta = 0.0
        trades: list[dict[str, Any]] = []
        delta_series: list[dict[str, Any]] = []

        for index, event in enumerate(events):
            profile.add_trade(event.price, event.quantity)
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

        return {
            "summary": {
                "source": "binance",
                "symbol": self.symbol,
                "connection_status": connection_status,
                "connection_message": connection_message,
                "trade_count": len(trades),
                "last_price": trades[-1]["price"] if trades else None,
                "cumulative_delta": cumulative_delta,
                "signals": 0,
                "orders": 0,
                "closed_positions": 0,
                "realized_pnl": 0,
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
        }
