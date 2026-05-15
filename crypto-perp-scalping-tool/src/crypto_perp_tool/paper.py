from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from crypto_perp_tool.config import default_settings
from crypto_perp_tool.journal import JsonlJournal, TradeLogger
from crypto_perp_tool.market_data import KlineEvent, TradeEvent
from crypto_perp_tool.profile import build_profile_levels
from crypto_perp_tool.risk import AccountState, RiskEngine
from crypto_perp_tool.signals import SignalEngine
from crypto_perp_tool.types import MarketSnapshot, ProfileLevelType, make_trade_record
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
    target_r_multiple: float = 5.0
    setup: str = "unknown"
    opened_at: int = 0
    initial_stop_price: float = 0.0
    max_favorable_move: float = 0.0
    max_adverse_move: float = 0.0


class PaperRunner:
    def __init__(self, equity: float, journal_path: Path | str, trade_log_path: Path | str | None = None, taker_fee_rate: float = 0.0004) -> None:
        self.settings = default_settings()
        self.equity = equity
        self.journal = JsonlJournal(journal_path, config_version=self.settings.config_version)
        self.trade_log = TradeLogger(trade_log_path) if trade_log_path is not None else None
        self.taker_fee_rate = taker_fee_rate
        self.risk = RiskEngine(self.settings.risk)
        self.signals = SignalEngine(
            self.settings.signals.min_reward_risk,
            self.settings.execution.max_data_lag_ms,
            session_gating_enabled=self.settings.signals.session_gating_enabled,
            reward_risk=self.settings.execution.reward_risk,
            dynamic_reward_risk_enabled=self.settings.execution.dynamic_reward_risk_enabled,
            reward_risk_min=self.settings.execution.reward_risk_min,
            reward_risk_max=self.settings.execution.reward_risk_max,
            atr_stop_mult=self.settings.execution.atr_stop_mult,
            min_stop_cost_mult=self.settings.execution.min_stop_cost_mult,
            min_target_cost_mult=self.settings.execution.min_target_cost_mult,
            taker_fee_rate=self.taker_fee_rate,
            execution_window=f"execution_{self.settings.profile.execution_window_minutes}m",
            micro_window=f"micro_{self.settings.profile.micro_window_minutes}m",
            context_window=f"context_{self.settings.profile.context_window_minutes}m",
        )

    def run_csv(self, path: Path | str, symbol: str = "BTCUSDT") -> PaperRunResult:
        events = list(self._load_csv(Path(path), symbol))
        if not events:
            return PaperRunResult(0, 0, 0, 0, 0, 0.0, str(self.journal.path))

        bin_size = self.settings.profile.btc_bin_size if symbol == "BTCUSDT" else self.settings.profile.eth_bin_size
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
                self._update_max_moves(position, event.price)
                exit_reason, close_price = self._close_trigger(position, event.price)
                if close_price is not None:
                    net_pnl = self._close_position(position, event.timestamp, close_price, exit_reason)
                    realized_pnl += net_pnl
                    closed_positions += 1
                    position = None

            rolling_delta.append(event.delta)
            if position is not None:
                continue

            levels = self._profile_levels(seen_events, event.timestamp, bin_size)
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
            signal = self.signals.evaluate(snapshot, klines=self._closed_1m_klines(seen_events, event.timestamp))
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
                    target_r_multiple=signal.target_r_multiple,
                    setup=signal.setup,
                    opened_at=signal.created_at,
                    initial_stop_price=signal.stop_price,
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
                        "target_r_multiple": signal.target_r_multiple,
                    },
                )
            else:
                rejected_count += 1

        if position is not None:
            final_event = events[-1]
            self._update_max_moves(position, final_event.price)
            net_pnl = self._close_position(position, final_event.timestamp, final_event.price, "end_of_replay")
            realized_pnl += net_pnl
            closed_positions += 1

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

    def _profile_levels(self, events: list[TradeEvent], timestamp: int, bin_size: float):
        settings = self.settings.profile
        trades = [(event.price, event.quantity, event.timestamp) for event in events]
        return (
            *build_profile_levels(
                trades,
                timestamp=timestamp,
                window_ms=settings.execution_window_minutes * 60 * 1000,
                label=f"execution_{settings.execution_window_minutes}m",
                bin_size=bin_size,
                value_area_ratio=settings.value_area_ratio,
                min_trades=settings.min_execution_profile_trades,
                min_bins=settings.min_profile_bins,
            ),
            *build_profile_levels(
                trades,
                timestamp=timestamp,
                window_ms=settings.micro_window_minutes * 60 * 1000,
                label=f"micro_{settings.micro_window_minutes}m",
                bin_size=bin_size,
                value_area_ratio=settings.value_area_ratio,
                min_trades=settings.min_micro_profile_trades,
                min_bins=settings.min_profile_bins,
            ),
            *build_profile_levels(
                trades,
                timestamp=timestamp,
                window_ms=settings.context_window_minutes * 60 * 1000,
                label=f"context_{settings.context_window_minutes}m",
                bin_size=bin_size,
                value_area_ratio=settings.value_area_ratio,
            ),
        )

    def _closed_1m_klines(self, events: list[TradeEvent], timestamp: int) -> tuple[KlineEvent, ...]:
        current_bucket = (timestamp // 60_000) * 60_000
        buckets: dict[int, list[TradeEvent]] = {}
        for event in events:
            bucket = (event.timestamp // 60_000) * 60_000
            if bucket >= current_bucket:
                continue
            buckets.setdefault(bucket, []).append(event)
        klines: list[KlineEvent] = []
        for bucket, bucket_events in sorted(buckets.items()):
            prices = [event.price for event in bucket_events]
            volume = sum(event.quantity for event in bucket_events)
            quote_volume = sum(event.quantity * event.price for event in bucket_events)
            klines.append(
                KlineEvent(
                    timestamp=bucket,
                    close_time=bucket + 59_999,
                    symbol=bucket_events[-1].symbol,
                    interval="1m",
                    open=bucket_events[0].price,
                    high=max(prices),
                    low=min(prices),
                    close=bucket_events[-1].price,
                    volume=volume,
                    quote_volume=quote_volume,
                    trade_count=len(bucket_events),
                    is_closed=True,
                )
            )
        return tuple(klines)

    def _close_trigger(self, position: PaperPosition, price: float) -> tuple[str | None, float | None]:
        if position.side == SignalSide.LONG:
            if price <= position.stop_price:
                return "stop_loss", position.stop_price
            if price >= position.target_price:
                return "target", position.target_price
        if position.side == SignalSide.SHORT:
            if price >= position.stop_price:
                return "stop_loss", position.stop_price
            if price <= position.target_price:
                return "target", position.target_price
        return None, None

    def _position_pnl(self, position: PaperPosition, close_price: float) -> float:
        if position.side == SignalSide.LONG:
            return (close_price - position.entry_price) * position.quantity
        return (position.entry_price - close_price) * position.quantity

    def _close_position(self, position: PaperPosition, timestamp: int, close_price: float, exit_reason: str) -> float:
        gross_pnl = self._position_pnl(position, close_price)
        entry_fee = abs(position.entry_price * position.quantity) * self.taker_fee_rate
        exit_fee = abs(close_price * position.quantity) * self.taker_fee_rate
        net_pnl = gross_pnl - entry_fee - exit_fee
        self.journal.write(
            "position_closed",
            {
                "signal_id": position.signal_id,
                "symbol": position.symbol,
                "side": position.side.value,
                "quantity": position.quantity,
                "entry_price": position.entry_price,
                "close_price": close_price,
                "stop_price": position.stop_price,
                "target_price": position.target_price,
                "target_r_multiple": position.target_r_multiple,
                "entry_fee": entry_fee,
                "exit_fee": exit_fee,
                "gross_pnl": gross_pnl,
                "net_pnl": net_pnl,
                "realized_pnl": net_pnl,
                "exit_reason": exit_reason,
            },
        )
        self._write_trade_record(position, timestamp, close_price, gross_pnl, net_pnl, exit_reason)
        return net_pnl

    def _update_max_moves(self, position: PaperPosition, price: float) -> None:
        if position.side == SignalSide.LONG:
            favorable = price - position.entry_price
            adverse = position.entry_price - price
        else:
            favorable = position.entry_price - price
            adverse = price - position.entry_price
        if favorable > position.max_favorable_move:
            position.max_favorable_move = favorable
        if adverse > position.max_adverse_move:
            position.max_adverse_move = adverse

    def _write_trade_record(
        self, position: PaperPosition, exit_time: int, exit_price: float, gross_pnl: float, net_pnl: float, exit_reason: str,
    ) -> None:
        if self.trade_log is None:
            return
        entry_fee = abs(position.entry_price * position.quantity) * self.taker_fee_rate
        exit_fee = abs(exit_price * position.quantity) * self.taker_fee_rate
        record = make_trade_record(
            signal_id=position.signal_id,
            setup=position.setup,
            symbol=position.symbol,
            side=position.side.value,
            entry_time=position.opened_at,
            entry_price=position.entry_price,
            quantity=position.quantity,
            entry_fee=entry_fee,
            signal_entry_price=position.entry_price,
            initial_stop_price=position.initial_stop_price or position.stop_price,
            stop_price=position.stop_price,
            target_price=position.target_price,
            exit_time=exit_time,
            exit_price=exit_price,
            exit_reason=exit_reason,
            exit_fee=exit_fee,
            gross_pnl=gross_pnl,
            net_pnl=net_pnl,
            max_favorable_move=position.max_favorable_move,
            max_adverse_move=position.max_adverse_move,
        )
        self.trade_log.write(record)
