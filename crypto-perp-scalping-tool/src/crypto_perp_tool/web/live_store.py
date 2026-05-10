from __future__ import annotations

import threading
from collections import deque
from typing import Any

from crypto_perp_tool.config import default_settings
from crypto_perp_tool.market_data import MarkPriceEvent, QuoteEvent, SpotPriceEvent, TradeEvent
from crypto_perp_tool.market_data.health import compute_health
from crypto_perp_tool.profile import VolumeProfileEngine
from crypto_perp_tool.risk.circuit import CircuitBreaker
from crypto_perp_tool.signals import SignalEngine
from crypto_perp_tool.types import HistoricalWindows
from crypto_perp_tool.web.details import empty_execution_details, mode_breakdown, total_pnl_for_range


class LiveOrderflowStore:
    def __init__(self, symbol: str, max_events: int = 20_000, display_events: int = 500,
                 enable_signals: bool = False) -> None:
        self.symbol = symbol.upper()
        self.max_events = max_events
        self.display_events = display_events
        self._events: deque[TradeEvent] = deque(maxlen=max_events)
        self._quote: QuoteEvent | None = None
        self._mark: MarkPriceEvent | None = None
        self._spot: SpotPriceEvent | None = None
        self._connection_status = "starting"
        self._connection_message = "waiting for Binance stream"
        self._reconnect_count = 0
        self._lock = threading.Lock()

        settings = default_settings()
        bin_size = settings.profile.btc_bin_size if self.symbol == "BTCUSDT" else settings.profile.eth_bin_size
        self._profile = VolumeProfileEngine(bin_size=bin_size, value_area_ratio=settings.profile.value_area_ratio)
        self._signal_engine = SignalEngine(
            min_reward_risk=settings.signals.min_reward_risk,
            max_data_lag_ms=settings.execution.max_data_lag_ms,
        ) if enable_signals else None
        self._circuit_breaker = CircuitBreaker()
        self._signal_count = 0
        self._order_count = 0
        self._closed_positions = 0
        self._realized_pnl = 0.0
        self._position: dict | None = None
        self._historical: HistoricalWindows = HistoricalWindows()

    def add_trade(self, event: TradeEvent) -> None:
        if event.symbol.upper() != self.symbol:
            return
        with self._lock:
            self._events.append(event)
            self._profile.add_trade(event.price, event.quantity)
            self._update_historical(event)
            self._try_signal(event)

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
            if status == "connected" and self._connection_status == "error":
                self._reconnect_count += 1

    def _update_historical(self, event: TradeEvent) -> None:
        self._historical = self._historical.with_window("spread_5min", 2.0)

    def _try_signal(self, event: TradeEvent) -> None:
        if self._signal_engine is None:
            return
        if self._circuit_breaker.state == "tripped":
            return
        events = list(self._events)
        if len(events) < 30:
            return
        window = events[-30:]
        delta_30s = sum(e.delta for e in window)
        volume_30s = sum(abs(e.delta) for e in window)
        prices = [e.price for e in events]
        avg_price = sum(prices) / len(prices)

        from crypto_perp_tool.types import MarketSnapshot
        now = event.timestamp
        levels = self._profile.levels(window="rolling_4h")

        snapshot = MarketSnapshot(
            exchange="binance_futures",
            symbol=self.symbol,
            event_time=now,
            local_time=now,
            last_price=event.price,
            bid_price=self._quote.bid_price if self._quote else event.price * 0.9999,
            ask_price=self._quote.ask_price if self._quote else event.price * 1.0001,
            spread_bps=((self._quote.ask_price - self._quote.bid_price) / self._quote.mid_price * 10000) if self._quote else 2.0,
            vwap=avg_price,
            atr_1m_14=max(event.price * 0.002, 1.0),
            delta_15s=sum(e.delta for e in events[-15:]),
            delta_30s=delta_30s,
            delta_60s=sum(e.delta for e in events[-60:]) if len(events) >= 60 else sum(e.delta for e in events),
            volume_30s=volume_30s,
            profile_levels=levels,
        )

        health = compute_health(
            connection_status=self._connection_status,
            last_event_time=now,
            last_local_time=now,
            reconnect_count=self._reconnect_count,
            symbol=self.symbol,
        )

        has_position = self._position is not None
        circuit_tripped = self._circuit_breaker.state == "tripped"

        signal = self._signal_engine.evaluate(
            snapshot,
            windows=self._historical,
            health=health,
            circuit_tripped=circuit_tripped,
            has_position=has_position,
            next_funding_time=self._mark.next_funding_time if self._mark else 0,
        )

        if signal is None:
            return

        self._signal_count += 1
        if self._position is None:
            self._position = {
                "signal_id": signal.id,
                "symbol": signal.symbol,
                "side": signal.side,
                "entry_price": signal.entry_price,
                "stop_price": signal.stop_price,
                "target_price": signal.target_price,
            }
            self._order_count += 1
        else:
            close_price = self._check_close(event.price)
            if close_price is not None:
                pnl = close_price - self._position["entry_price"]
                if self._position["side"].value == "short":
                    pnl = self._position["entry_price"] - close_price
                self._realized_pnl += pnl
                self._closed_positions += 1
                self._position = None

    def _check_close(self, current_price: float) -> float | None:
        if self._position is None:
            return None
        side = self._position["side"].value
        if side == "long":
            if current_price <= self._position["stop_price"]:
                return self._position["stop_price"]
            if current_price >= self._position["target_price"]:
                return self._position["target_price"]
        else:
            if current_price >= self._position["stop_price"]:
                return self._position["stop_price"]
            if current_price <= self._position["target_price"]:
                return self._position["target_price"]
        return None

    def view(self) -> dict[str, Any]:
        with self._lock:
            events = list(self._events)
            quote = self._quote
            mark = self._mark
            spot = self._spot
            connection_status = self._connection_status
            connection_message = self._connection_message

        cumulative_delta = 0.0
        trades: list[dict[str, Any]] = []
        delta_series: list[dict[str, Any]] = []
        display_events = events[-self.display_events:]
        details = empty_execution_details()

        for index, event in enumerate(display_events):
            cumulative_delta += event.delta
            trades.append({
                "index": index, "timestamp": event.timestamp, "symbol": event.symbol,
                "price": event.price, "quantity": event.quantity,
                "side": "sell" if event.is_buyer_maker else "buy", "delta": event.delta,
            })
            delta_series.append({
                "index": index, "timestamp": event.timestamp,
                "delta": event.delta, "cumulative_delta": cumulative_delta,
            })

        last_trade_price = trades[-1]["price"] if trades else None
        quote_mid_price = quote.mid_price if quote is not None else None
        spot_last_price = spot.price if spot is not None else None
        index_price = mark.index_price if mark is not None else None
        last_price = (
            spot_last_price if spot_last_price is not None
            else index_price if index_price is not None
            else last_trade_price if last_trade_price is not None
            else quote_mid_price
        )
        price_source = (
            "spotTrade" if spot_last_price is not None
            else "indexPrice" if index_price is not None
            else "aggTrade" if last_trade_price is not None
            else "bookTicker"
        )
        derived_connection_status = "connected" if last_price is not None else connection_status

        levels = self._profile.levels(window="rolling_4h")

        return {
            "summary": {
                "source": "binance",
                "symbol": self.symbol,
                "connection_status": derived_connection_status,
                "connection_message": connection_message,
                "trade_count": len(trades),
                "profile_trade_count": len(events),
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
                "signals": self._signal_count,
                "orders": self._order_count,
                "closed_positions": self._closed_positions,
                "realized_pnl": self._realized_pnl,
                "circuit_state": self._circuit_breaker.state,
                "pnl_24h": total_pnl_for_range(details, "24h"),
                "mode_breakdown": mode_breakdown(details),
            },
            "trades": trades,
            "delta_series": delta_series,
            "profile_levels": [
                {"type": level.type.value, "price": level.price,
                 "lower_bound": level.lower_bound, "upper_bound": level.upper_bound,
                 "strength": level.strength, "window": level.window}
                for level in levels
            ],
            "markers": [],
            "details": details,
        }
