from __future__ import annotations

from crypto_perp_tool.types import SignalSide, TradeSignal


def entry_limit_price(signal: TradeSignal, pullback_bps: float) -> float:
    adjustment = pullback_bps / 10_000
    if signal.side == SignalSide.LONG:
        return signal.entry_price * (1 - adjustment)
    return signal.entry_price * (1 + adjustment)


def pending_entry_touched(side: SignalSide, limit_price: float, price: float) -> bool:
    if side == SignalSide.LONG:
        return price <= limit_price
    return price >= limit_price


def entry_limit_fill_price(signal: TradeSignal, limit_price: float, event_price: float) -> float:
    if signal.side == SignalSide.LONG:
        return min(limit_price, event_price)
    return max(limit_price, event_price)


def entry_fill_price(signal: TradeSignal, slippage_bps: float, reference_price: float | None = None) -> float:
    reference = signal.entry_price if reference_price is None else reference_price
    adjustment = slippage_bps / 10_000
    if signal.side == SignalSide.LONG:
        return reference * (1 + adjustment)
    return reference * (1 - adjustment)


def exit_fill_price(side: SignalSide, trigger_price: float, slippage_bps: float) -> float:
    adjustment = slippage_bps / 10_000
    if side == SignalSide.LONG:
        return trigger_price * (1 - adjustment)
    return trigger_price * (1 + adjustment)


def fee(price: float, quantity: float, fee_rate: float) -> float:
    return abs(price * quantity) * fee_rate


def position_pnl(side: SignalSide, entry_price: float, quantity: float, close_price: float) -> float:
    if side == SignalSide.LONG:
        return (close_price - entry_price) * quantity
    return (entry_price - close_price) * quantity
