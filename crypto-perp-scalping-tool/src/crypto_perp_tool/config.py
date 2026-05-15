import hashlib
import json
from dataclasses import dataclass
import os
from typing import Any

from crypto_perp_tool.serialization import to_jsonable


@dataclass(frozen=True)
class RiskSettings:
    risk_per_trade: float = 0.0025
    daily_loss_limit: float = 0.01
    max_consecutive_losses: int = 3
    max_leverage: int = 3
    max_symbol_notional_equity_multiple: float = 2.0


@dataclass(frozen=True)
class ExecutionSettings:
    entry_timeout_seconds: int = 10
    websocket_stale_ms: int = 1500
    max_data_lag_ms: int = 2000
    btc_max_slippage_bps: int = 3
    eth_max_slippage_bps: int = 4
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


@dataclass(frozen=True)
class ProfileSettings:
    session_timezone: str = "UTC"
    value_area_ratio: float = 0.70
    execution_window_minutes: int = 30
    micro_window_minutes: int = 15
    context_window_minutes: int = 60
    btc_bin_size: int = 20
    eth_bin_size: int = 5
    min_execution_profile_trades: int = 50
    min_micro_profile_trades: int = 25
    min_profile_bins: int = 3
    asia_start_hour: int = 0
    asia_end_hour: int = 7
    london_start_hour: int = 7
    london_end_hour: int = 12
    london_end_minute: int = 30
    ny_start_hour: int = 12
    ny_start_minute: int = 30
    ny_end_hour: int = 20


@dataclass(frozen=True)
class SignalSettings:
    min_reward_risk: float = 1.2
    delta_window_seconds: tuple[int, ...] = (15, 30, 60)
    funding_blackout_minutes: int = 2
    aggression_large_threshold: float = 20.0
    aggression_block_threshold: float = 50.0
    atr_period: int = 14
    session_gating_enabled: bool = True
    aggression_percentile_large: float = 0.95
    aggression_percentile_block: float = 0.99
    aggression_half_life_minutes: int = 1440
    aggression_dynamic_enabled: bool = True


@dataclass(frozen=True)
class Settings:
    exchange: str
    mode: str
    symbols: tuple[str, ...]
    risk: RiskSettings
    execution: ExecutionSettings
    profile: ProfileSettings
    signals: SignalSettings
    safety_warnings: tuple[str, ...] = ()
    config_version: str = ""


def _compute_config_version(settings: Settings) -> str:
    """Generate a short hash of strategy-critical parameters for version tracking."""
    payload = to_jsonable({
        "risk": settings.risk,
        "execution": settings.execution,
        "profile": settings.profile,
        "signals": settings.signals,
    })
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()[:12]


def default_settings() -> Settings:
    base = Settings(
        exchange="binance_futures",
        mode="paper",
        symbols=("BTCUSDT", "ETHUSDT"),
        risk=RiskSettings(),
        execution=ExecutionSettings(),
        profile=ProfileSettings(),
        signals=SignalSettings(),
    )
    return Settings(
        exchange=base.exchange,
        mode=base.mode,
        symbols=base.symbols,
        risk=base.risk,
        execution=base.execution,
        profile=base.profile,
        signals=base.signals,
        config_version=_compute_config_version(base),
    )


def load_settings(overrides: dict[str, Any] | None = None) -> Settings:
    overrides = overrides or {}
    base = default_settings()
    requested_mode = str(overrides.get("mode", base.mode))
    warnings: list[str] = []
    mode = requested_mode

    if requested_mode == "live" and os.getenv("LIVE_TRADING_CONFIRMATION") != "I_UNDERSTAND_LIVE_RISK":
        mode = "paper"
        warnings.append("live_guard_missing_confirmation")

    symbols = overrides.get("symbols", base.symbols)
    if isinstance(symbols, list):
        symbols = tuple(symbols)

    settings = Settings(
        exchange=str(overrides.get("exchange", base.exchange)),
        mode=mode,
        symbols=symbols,
        risk=base.risk,
        execution=base.execution,
        profile=base.profile,
        signals=base.signals,
        safety_warnings=tuple(warnings),
    )
    return Settings(
        exchange=settings.exchange,
        mode=settings.mode,
        symbols=settings.symbols,
        risk=settings.risk,
        execution=settings.execution,
        profile=settings.profile,
        signals=settings.signals,
        safety_warnings=settings.safety_warnings,
        config_version=_compute_config_version(settings),
    )
