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
    target_r_multiple: float
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
    setup_model: str = ""
    legacy_setup: str = ""
    market_state: str = ""
    bias: str = ""
    target_source: str = ""
    management_profile: str = ""


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
    reward_risk: float = 5.0
    dynamic_reward_risk_enabled: bool = True
    reward_risk_min: float = 3.0
    reward_risk_max: float = 10.0
    atr_stop_mult: float = 0.35
    kline_stop_shift_consecutive_bars: int = 3
    kline_stop_shift_reference_bars: int = 2
    min_stop_cost_mult: float = 1.0
    min_target_cost_mult: float = 2.0
    max_holding_ms: int = 900_000
    squeeze_break_even_r: float = 1.25
    failed_auction_break_even_r: float = 1.5
    lvn_acceptance_break_even_r: float = 1.5
    first_structure_reduce_ratio: float = 0.5
    absorption_reduce_ratio: float = 0.5
    no_followthrough_seconds: int = 45
