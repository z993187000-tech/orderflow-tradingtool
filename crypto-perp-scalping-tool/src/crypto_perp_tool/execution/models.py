from __future__ import annotations

from dataclasses import dataclass

from crypto_perp_tool.types import SignalSide, TradeSignal


@dataclass
class PaperOpenPosition:
    signal_id: str
    symbol: str
    side: SignalSide
    setup: str
    quantity: float
    entry_price: float
    signal_entry_price: float
    stop_price: float
    initial_stop_price: float
    target_price: float
    opened_at: int
    entry_fee: float = 0.0
    initial_quantity: float = 0.0
    break_even_shifted: bool = False
    absorption_reduced: bool = False
    first_take_profit_done: bool = False
    trail_stop_price: float | None = None
    max_favorable_move: float = 0.0
    max_adverse_move: float = 0.0
    entry_order_type: str = "limit"


@dataclass
class PaperPendingEntry:
    signal: TradeSignal
    quantity: float
    limit_price: float
    created_at: int
    expires_at: int


@dataclass(frozen=True)
class PaperExecutionConfig:
    entry_slippage_bps: float = 0.0
    exit_slippage_bps: float = 0.0
    partial_fill_ratio: float = 1.0
    stop_submission_success: bool = True
    pending_entry_timeout_ms: int = 7_000
    limit_entry_pullback_bps: float = 1.0
    post_close_cooldown_ms: int = 30_000
    first_take_profit_r: float = 1.0
    first_take_profit_ratio: float = 0.5
    trail_after_r: float = 1.0
    trail_atr_multiple: float = 0.35
    max_holding_ms: int = 180_000
