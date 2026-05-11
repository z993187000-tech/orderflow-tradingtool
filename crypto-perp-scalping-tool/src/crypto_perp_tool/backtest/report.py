from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class BacktestReport:
    total_trades: int
    wins: int
    losses: int
    win_rate: float
    net_pnl: float
    gross_profit: float
    gross_loss: float
    profit_factor: float
    average_r: float
    max_drawdown: float
    max_daily_loss: float
    max_consecutive_losses: int
    average_holding_time_ms: float
    average_slippage_bps: float
    by_setup: dict[str, dict[str, Any]] = field(default_factory=dict)
    data_quality: dict[str, str] = field(default_factory=dict)


class BacktestReporter:
    def __init__(self, initial_equity: float = 10_000) -> None:
        self.initial_equity = initial_equity

    def from_details(self, details: dict[str, Any]) -> BacktestReport:
        paper = details.get("paper") or {}
        closed = list(paper.get("closed_positions") or [])
        orders = list(paper.get("orders") or [])
        signals = list(paper.get("signals") or [])
        pnls = [float(item.get("net_realized_pnl", item.get("realized_pnl", 0.0))) for item in closed]

        gross_profit = sum(value for value in pnls if value > 0)
        gross_loss = abs(sum(value for value in pnls if value < 0))
        total_trades = len(closed)
        wins = sum(1 for value in pnls if value > 0)
        losses = sum(1 for value in pnls if value < 0)
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else (0.0 if gross_profit == 0 else float("inf"))

        return BacktestReport(
            total_trades=total_trades,
            wins=wins,
            losses=losses,
            win_rate=wins / total_trades if total_trades else 0.0,
            net_pnl=sum(pnls),
            gross_profit=gross_profit,
            gross_loss=gross_loss,
            profit_factor=profit_factor,
            average_r=self._average_r(closed),
            max_drawdown=self._max_drawdown(pnls),
            max_daily_loss=self._max_daily_loss(closed),
            max_consecutive_losses=self._max_consecutive_losses(pnls),
            average_holding_time_ms=self._average_holding_time(closed),
            average_slippage_bps=self._average_slippage(orders),
            by_setup=self._by_setup(closed, signals),
            data_quality=self._data_quality(closed, orders, signals),
        )

    def _average_r(self, closed: list[dict[str, Any]]) -> float:
        values: list[float] = []
        for item in closed:
            entry = float(item.get("entry_price") or 0)
            stop = float(item.get("stop_price") or 0)
            quantity = float(item.get("quantity") or 1)
            risk = abs(entry - stop) * quantity
            if risk > 0:
                pnl = float(item.get("net_realized_pnl", item.get("realized_pnl", 0.0)))
                values.append(pnl / risk)
        return sum(values) / len(values) if values else 0.0

    def _max_drawdown(self, pnls: list[float]) -> float:
        cumulative = 0.0
        peak = 0.0
        max_drawdown = 0.0
        for pnl in pnls:
            cumulative += pnl
            peak = max(peak, cumulative)
            max_drawdown = max(max_drawdown, peak - cumulative)
        return max_drawdown

    def _max_daily_loss(self, closed: list[dict[str, Any]]) -> float:
        by_day: dict[int, float] = {}
        for item in closed:
            timestamp = int(item.get("timestamp") or 0)
            day = timestamp // 86_400_000
            by_day[day] = by_day.get(day, 0.0) + float(item.get("net_realized_pnl", item.get("realized_pnl", 0.0)))
        losses = [abs(value) for value in by_day.values() if value < 0]
        return max(losses, default=0.0)

    def _max_consecutive_losses(self, pnls: list[float]) -> int:
        current = 0
        maximum = 0
        for pnl in pnls:
            current = current + 1 if pnl < 0 else 0
            maximum = max(maximum, current)
        return maximum

    def _average_holding_time(self, closed: list[dict[str, Any]]) -> float:
        durations = [
            int(item["timestamp"]) - int(item["opened_at"])
            for item in closed
            if item.get("timestamp") is not None and item.get("opened_at") is not None
        ]
        return sum(durations) / len(durations) if durations else 0.0

    def _average_slippage(self, orders: list[dict[str, Any]]) -> float:
        values = [float(item["slippage_bps"]) for item in orders if item.get("slippage_bps") is not None]
        return sum(values) / len(values) if values else 0.0

    def _by_setup(self, closed: list[dict[str, Any]], signals: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        signal_setups = [str(item.get("setup") or "unknown") for item in signals]
        setups = set(signal_setups) | {str(item.get("setup") or "unknown") for item in closed}
        output: dict[str, dict[str, Any]] = {}
        for setup in setups:
            setup_closed = [item for item in closed if str(item.get("setup") or "unknown") == setup]
            pnls = [float(item.get("net_realized_pnl", item.get("realized_pnl", 0.0))) for item in setup_closed]
            output[setup] = {
                "signals": signal_setups.count(setup),
                "trades": len(setup_closed),
                "wins": sum(1 for pnl in pnls if pnl > 0),
                "net_pnl": sum(pnls),
            }
        return output

    def _data_quality(
        self,
        closed: list[dict[str, Any]],
        orders: list[dict[str, Any]],
        signals: list[dict[str, Any]],
    ) -> dict[str, str]:
        return {
            "closed_positions": "present" if closed else "missing",
            "orders": "present" if orders else "missing",
            "signals": "present" if signals else "missing",
            "slippage": "present" if any(order.get("slippage_bps") is not None for order in orders) else "missing",
        }
