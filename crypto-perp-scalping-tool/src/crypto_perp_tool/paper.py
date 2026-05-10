from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from crypto_perp_tool.config import default_settings
from crypto_perp_tool.journal import JsonlJournal
from crypto_perp_tool.market_data import TradeEvent
from crypto_perp_tool.profile import VolumeProfileEngine
from crypto_perp_tool.risk import AccountState, RiskEngine
from crypto_perp_tool.signals import SignalEngine
from crypto_perp_tool.types import MarketSnapshot, ProfileLevelType
from crypto_perp_tool.types import SignalSide


@dataclass(frozen=True)
class PaperRunResult:
    trades: int
    signals: int
    orders: int
    rejected: int
    closed_positions: int
    realized_pnl: float
    journal_path: str


@dataclass
class PaperPosition:
    signal_id: str
    symbol: str
    side: SignalSide
    quantity: float
    entry_price: float
    stop_price: float
    target_price: float


class PaperRunner:
    def __init__(self, equity: float, journal_path: Path | str) -> None:
        self.settings = default_settings()
        self.equity = equity
        self.journal = JsonlJournal(journal_path)
        self.risk = RiskEngine(self.settings.risk)
        self.signals = SignalEngine(self.settings.signals.min_reward_risk, self.settings.execution.max_data_lag_ms)

    def run_csv(self, path: Path | str, symbol: str = "BTCUSDT") -> PaperRunResult:
        events = list(self._load_csv(Path(path), symbol))
        if not events:
            return PaperRunResult(0, 0, 0, 0, 0, 0.0, str(self.journal.path))

        bin_size = self.settings.profile.btc_bin_size if symbol == "BTCUSDT" else self.settings.profile.eth_bin_size
        profile = VolumeProfileEngine(bin_size=bin_size, value_area_ratio=self.settings.profile.value_area_ratio)
        rolling_delta: list[float] = []
        signal_count = 0
        order_count = 0
        rejected_count = 0
        closed_positions = 0
        realized_pnl = 0.0
        position: PaperPosition | None = None

        seen_events: list[TradeEvent] = []
        for event in events:
            seen_events.append(event)
            if position is not None:
                close_price = self._close_price_if_triggered(position, event.price)
                if close_price is not None:
                    pnl = self._position_pnl(position, close_price)
                    realized_pnl += pnl
                    closed_positions += 1
                    self.journal.write(
                        "position_closed",
                        {
                            "signal_id": position.signal_id,
                            "symbol": position.symbol,
                            "side": position.side.value,
                            "quantity": position.quantity,
                            "entry_price": position.entry_price,
                            "close_price": close_price,
                            "realized_pnl": pnl,
                        },
                    )
                    position = None

            profile.add_trade(event.price, event.quantity, event.timestamp)
            rolling_delta.append(event.delta)
            levels = profile.levels("rolling_4h")
            if not any(level.type == ProfileLevelType.LVN for level in levels):
                continue

            delta_30s = sum(rolling_delta[-30:])
            snapshot = MarketSnapshot(
                exchange=self.settings.exchange,
                symbol=event.symbol,
                event_time=event.timestamp,
                local_time=event.timestamp,
                last_price=event.price,
                bid_price=event.price * 0.9999,
                ask_price=event.price * 1.0001,
                spread_bps=2.0,
                vwap=self._vwap(seen_events),
                atr_1m_14=max(event.price * 0.002, bin_size / 2),
                delta_15s=sum(rolling_delta[-15:]),
                delta_30s=delta_30s,
                delta_60s=sum(rolling_delta[-60:]),
                volume_30s=sum(abs(delta) for delta in rolling_delta[-30:]),
                profile_levels=levels,
            )
            signal = self.signals.evaluate(snapshot)
            if signal is None:
                continue

            signal_count += 1
            self.journal.write("signal", {"signal": signal})
            decision = self.risk.evaluate(
                signal,
                AccountState(equity=self.equity, realized_pnl_today=0, consecutive_losses=0),
            )
            self.journal.write("risk_decision", {"decision": decision})
            if decision.allowed:
                order_count += 1
                position = PaperPosition(
                    signal_id=signal.id,
                    symbol=signal.symbol,
                    side=signal.side,
                    quantity=decision.quantity,
                    entry_price=signal.entry_price,
                    stop_price=signal.stop_price,
                    target_price=signal.target_price,
                )
                self.journal.write(
                    "paper_fill",
                    {
                        "signal_id": signal.id,
                        "symbol": signal.symbol,
                        "side": signal.side.value,
                        "quantity": decision.quantity,
                        "fill_price": signal.entry_price,
                    },
                )
                self.journal.write(
                    "paper_order",
                    {
                        "signal_id": signal.id,
                        "symbol": signal.symbol,
                        "side": signal.side.value,
                        "quantity": decision.quantity,
                        "entry_price": signal.entry_price,
                        "stop_price": signal.stop_price,
                        "target_price": signal.target_price,
                    },
                )
            else:
                rejected_count += 1

        return PaperRunResult(
            len(events),
            signal_count,
            order_count,
            rejected_count,
            closed_positions,
            realized_pnl,
            str(self.journal.path),
        )

    def _load_csv(self, path: Path, symbol: str) -> list[TradeEvent]:
        with path.open("r", encoding="utf-8", newline="") as handle:
            rows = csv.DictReader(handle)
            required = {"timestamp", "price", "quantity"}
            fieldnames = set(rows.fieldnames or [])
            missing = sorted(required - fieldnames)
            if missing:
                raise ValueError(f"missing required columns: {', '.join(missing)}")
            return [
                TradeEvent(
                    timestamp=int(row["timestamp"]),
                    symbol=row.get("symbol") or symbol,
                    price=float(row["price"]),
                    quantity=float(row["quantity"]),
                    is_buyer_maker=str(row.get("is_buyer_maker", "false")).lower() == "true",
                )
                for row in rows
            ]

    def _vwap(self, events: list[TradeEvent]) -> float:
        total_quantity = sum(event.quantity for event in events)
        if total_quantity <= 0:
            return 0
        return sum(event.price * event.quantity for event in events) / total_quantity

    def _close_price_if_triggered(self, position: PaperPosition, price: float) -> float | None:
        if position.side == SignalSide.LONG:
            if price <= position.stop_price:
                return position.stop_price
            if price >= position.target_price:
                return position.target_price
        if position.side == SignalSide.SHORT:
            if price >= position.stop_price:
                return position.stop_price
            if price <= position.target_price:
                return position.target_price
        return None

    def _position_pnl(self, position: PaperPosition, close_price: float) -> float:
        if position.side == SignalSide.LONG:
            return (close_price - position.entry_price) * position.quantity
        return (position.entry_price - close_price) * position.quantity
