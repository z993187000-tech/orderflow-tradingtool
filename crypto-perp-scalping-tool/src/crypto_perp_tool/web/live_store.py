from __future__ import annotations

import threading
from collections import deque
from pathlib import Path
from typing import Any

from crypto_perp_tool.config import default_settings
from crypto_perp_tool.execution import PaperTradingEngine
from crypto_perp_tool.market_data import MarkPriceEvent, QuoteEvent, SpotPriceEvent, TradeEvent
from crypto_perp_tool.profile import VolumeProfileEngine
from crypto_perp_tool.web.details import empty_execution_details, mode_breakdown, total_pnl_for_range


class LiveOrderflowStore:
    def __init__(
        self,
        symbol: str,
        max_events: int = 20_000,
        display_events: int = 500,
        paper_journal_path: Path | str | None = None,
    ) -> None:
        self.symbol = symbol.upper()
        self.max_events = max_events
        self.display_events = display_events
        self._events: deque[TradeEvent] = deque(maxlen=max_events)
        self._quote: QuoteEvent | None = None
        self._mark: MarkPriceEvent | None = None
        self._spot: SpotPriceEvent | None = None
        self._paper = PaperTradingEngine(symbol=self.symbol, journal_path=paper_journal_path)
        self._connection_status = "starting"
        self._connection_message = "waiting for Binance stream"
        self._lock = threading.Lock()

    def add_trade(self, event: TradeEvent, received_at: int | None = None) -> None:
        if event.symbol.upper() != self.symbol:
            return
        with self._lock:
            self._events.append(event)
            self._paper.process_trade(event, self._quote, received_at=received_at)

    def add_quote(self, event: QuoteEvent) -> None:
        if event.symbol.upper() != self.symbol:
            return
        with self._lock:
            self._quote = event

    def add_mark(self, event: MarkPriceEvent) -> None:
        if event.symbol.upper() != self.symbol:
            return
        with self._lock:
            self._mark = event

    def add_spot(self, event: SpotPriceEvent) -> None:
        if event.symbol.upper() != self.symbol:
            return
        with self._lock:
            self._spot = event

    def set_connection_status(self, status: str, message: str) -> None:
        with self._lock:
            self._connection_status = status
            self._connection_message = message

    def view(self) -> dict[str, Any]:
        with self._lock:
            events = list(self._events)
            quote = self._quote
            mark = self._mark
            spot = self._spot
            details = self._paper.details()
            paper_summary = self._paper.summary()
            markers = self._paper.markers()
            connection_status = self._connection_status
            connection_message = self._connection_message

        settings = default_settings()
        bin_size = settings.profile.btc_bin_size if self.symbol == "BTCUSDT" else settings.profile.eth_bin_size
        profile = VolumeProfileEngine(bin_size=bin_size, value_area_ratio=settings.profile.value_area_ratio)
        latest_event_time = events[-1].timestamp if events else 0
        rolling_window_ms = settings.profile.rolling_window_minutes * 60 * 1000
        profile_events = [
            event for event in events if latest_event_time and latest_event_time - event.timestamp <= rolling_window_ms
        ]
        cumulative_delta = 0.0
        trades: list[dict[str, Any]] = []
        delta_series: list[dict[str, Any]] = []
        display_events = events[-self.display_events :]
        if not details:
            details = empty_execution_details()

        for event in profile_events:
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

        markers = _attach_marker_indexes(markers, trades)
        last_trade_price = trades[-1]["price"] if trades else None
        quote_mid_price = quote.mid_price if quote is not None else None
        spot_last_price = spot.price if spot is not None else None
        index_price = mark.index_price if mark is not None else None
        last_price = last_trade_price if last_trade_price is not None else index_price if index_price is not None else quote_mid_price
        price_source = (
            "aggTrade"
            if last_trade_price is not None
            else "indexPrice"
            if index_price is not None
            else "bookTicker"
        )
        derived_connection_status = "connected" if last_price is not None else connection_status
        return {
            "summary": {
                "source": "binance",
                "symbol": self.symbol,
                "connection_status": derived_connection_status,
                "connection_message": connection_message,
                "trade_count": len(trades),
                "seen_trade_count": len(events),
                "profile_trade_count": len(profile_events),
                "last_price": last_price,
                "spot_last_price": spot_last_price,
                "last_trade_price": last_trade_price,
                "bid_price": quote.bid_price if quote is not None else None,
                "ask_price": quote.ask_price if quote is not None else None,
                "quote_mid_price": quote_mid_price,
                "mark_price": mark.mark_price if mark is not None else None,
                "index_price": index_price,
                "funding_rate": mark.funding_rate if mark is not None else None,
                "next_funding_time": mark.next_funding_time if mark is not None else None,
                "price_source": price_source,
                "cumulative_delta": cumulative_delta,
                "signals": paper_summary["signals"],
                "orders": paper_summary["orders"],
                "closed_positions": paper_summary["closed_positions"],
                "realized_pnl": paper_summary["realized_pnl"],
                "open_position": paper_summary["open_position"],
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
            "markers": markers,
            "details": details,
        }


def _attach_marker_indexes(markers: list[dict[str, Any]], trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not trades:
        return []
    attached = []
    for marker in markers:
        price = marker.get("price")
        if price is None:
            continue
        copy = dict(marker)
        copy["index"] = min(range(len(trades)), key=lambda index: abs(trades[index]["price"] - price))
        attached.append(copy)
    return attached
