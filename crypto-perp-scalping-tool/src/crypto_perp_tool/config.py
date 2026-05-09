from dataclasses import dataclass
import os
from typing import Any


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


@dataclass(frozen=True)
class ProfileSettings:
    session_timezone: str = "UTC"
    value_area_ratio: float = 0.70
    rolling_window_minutes: int = 240
    btc_bin_size: int = 10
    eth_bin_size: int = 2


@dataclass(frozen=True)
class SignalSettings:
    min_reward_risk: float = 1.2
    delta_window_seconds: tuple[int, ...] = (15, 30, 60)
    funding_blackout_minutes: int = 2


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


def default_settings() -> Settings:
    return Settings(
        exchange="binance_futures",
        mode="paper",
        symbols=("BTCUSDT", "ETHUSDT"),
        risk=RiskSettings(),
        execution=ExecutionSettings(),
        profile=ProfileSettings(),
        signals=SignalSettings(),
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

    return Settings(
        exchange=str(overrides.get("exchange", base.exchange)),
        mode=mode,
        symbols=symbols,
        risk=base.risk,
        execution=base.execution,
        profile=base.profile,
        signals=base.signals,
        safety_warnings=tuple(warnings),
    )
