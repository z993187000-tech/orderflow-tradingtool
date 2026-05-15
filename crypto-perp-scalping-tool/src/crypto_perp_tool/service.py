from __future__ import annotations

from dataclasses import asdict, replace
from typing import Any

from crypto_perp_tool.config import RiskSettings, Settings, default_settings
from crypto_perp_tool.journal import JsonlJournal


_RISK_KEYS = {"risk_per_trade", "max_leverage", "max_symbol_notional"}
_STORE_KEYS = {"equity", "cooldown_ms", "flash_atr_mult", "flash_pct"}
_STRATEGY_KEYS = {
    "reward_risk",
    "dynamic_reward_risk_enabled",
    "reward_risk_min",
    "reward_risk_max",
    "atr_stop_mult",
    "min_stop_cost_mult",
    "min_target_cost_mult",
    "max_holding_min",
}
_ALL_KEYS = _RISK_KEYS | _STORE_KEYS | _STRATEGY_KEYS


def _validate_risk_setting(key: str, value: float | int) -> tuple[float | int | None, str | None]:
    """Validate and coerce a setting value. Returns (coerced, error)."""
    if key in ("risk_per_trade",):
        v = float(value)
        if not (0.0001 <= v <= 0.05):
            return None, f"{key} must be 0.0001–0.05 (0.01%–5%)"
        return v, None
    if key in ("max_leverage",):
        v = int(value)
        if not (1 <= v <= 20):
            return None, f"{key} must be 1–20"
        return v, None
    if key in ("max_symbol_notional",):
        v = float(value)
        if not (0.5 <= v <= 10.0):
            return None, f"{key} must be 0.5–10.0"
        return v, None
    if key in ("equity",):
        v = float(value)
        if v <= 0:
            return None, f"{key} must be positive"
        return v, None
    if key in ("cooldown_ms",):
        v = int(value)
        if not (30_000 <= v <= 3_600_000):
            return None, f"{key} must be 30000–3600000 ms (30s–1h)"
        return v, None
    if key in ("flash_atr_mult",):
        v = float(value)
        if not (1.0 <= v <= 20.0):
            return None, f"{key} must be 1.0–20.0"
        return v, None
    if key in ("flash_pct",):
        v = float(value)
        if not (0.001 <= v <= 0.05):
            return None, f"{key} must be 0.001–0.05 (0.1%–5%)"
        return v, None
    if key in ("dynamic_reward_risk_enabled",):
        return bool(value), None
    if key in ("reward_risk", "reward_risk_min", "reward_risk_max"):
        v = float(value)
        if not (1.0 <= v <= 20.0):
            return None, f"{key} must be 1.0–20.0"
        return v, None
    if key in ("atr_stop_mult",):
        v = float(value)
        if not (0.1 <= v <= 2.0):
            return None, f"{key} must be 0.1–2.0"
        return v, None
    if key in ("min_stop_cost_mult",):
        v = float(value)
        if not (1.0 <= v <= 10.0):
            return None, f"{key} must be 1.0–10.0"
        return v, None
    if key in ("min_target_cost_mult",):
        v = float(value)
        if not (1.0 <= v <= 20.0):
            return None, f"{key} must be 1.0–20.0"
        return v, None
    if key in ("max_holding_min",):
        v = int(value)
        if not (1 <= v <= 60):
            return None, f"{key} must be 1–60 minutes"
        return v, None
    return None, f"unknown setting: {key}"


class TradingService:
    def __init__(self, journal: JsonlJournal, settings: Settings | None = None) -> None:
        self.settings = settings or default_settings()
        self.journal = journal
        self.paused = False
        self._store: Any = None

    def set_store(self, store: Any) -> None:
        self._store = store

    def status(self) -> str:
        paused = "true" if self.paused else "false"
        return f"mode={self.settings.mode} exchange={self.settings.exchange} symbols={','.join(self.settings.symbols)} paused={paused}"

    def pause(self, actor: str) -> str:
        self.paused = True
        self.journal.write("operator_command", {"actor": actor, "command": "pause"})
        return "new entries paused; protective exits remain active"

    def resume(self, actor: str) -> str:
        self.paused = False
        self.journal.write("operator_command", {"actor": actor, "command": "resume"})
        return "paper trading entries resumed"

    def risk(self) -> dict[str, object]:
        return asdict(self.settings.risk)

    def recent_journal(self, limit: int = 5) -> list[dict[str, object]]:
        return self.journal.tail(limit)

    def update_setting(self, key: str, raw_value: str) -> str:
        key = key.strip().lower()
        if key == "dynamic_reward_risk_enabled":
            raw_bool = raw_value.strip().lower()
            if raw_bool in {"true", "1", "yes", "on"}:
                value = True
            elif raw_bool in {"false", "0", "no", "off"}:
                value = False
            else:
                return f"invalid value: {raw_value}"
        else:
            try:
                value = float(raw_value)
                if key in ("max_leverage", "cooldown_ms"):
                    value = int(value)
            except ValueError:
                return f"invalid value: {raw_value}"

        coerced, error = _validate_risk_setting(key, value)
        if error:
            return error

        if key in _RISK_KEYS:
            return self._update_risk(key, coerced)
        if key in _STORE_KEYS or key in _STRATEGY_KEYS:
            return self._update_store(key, coerced)
        return f"unknown setting: {key}"

    def _update_risk(self, key: str, value: float | int) -> str:
        old_risk = self.settings.risk
        kwargs: dict[str, Any] = {}
        if key == "max_symbol_notional":
            kwargs["max_symbol_notional_equity_multiple"] = float(value)
        else:
            kwargs[key] = value
        new_risk = replace(old_risk, **kwargs)
        self.settings = replace(self.settings, risk=new_risk)
        self.journal.write("risk_setting_updated", {"key": key, "value": value, "actor": "telegram"})
        if self._store is not None and hasattr(self._store, "update_risk_settings"):
            self._store.update_risk_settings(new_risk)
        return f"{key} = {value}"

    def _update_store(self, key: str, value: float | int) -> str:
        self.journal.write("store_setting_updated", {"key": key, "value": value, "actor": "telegram"})
        if self._store is None:
            return "not connected to trading engine"
        if key == "equity":
            if hasattr(self._store, "update_equity"):
                self._store.update_equity(float(value))
        elif key == "cooldown_ms":
            if hasattr(self._store, "update_circuit_cooldown"):
                self._store.update_circuit_cooldown(int(value))
        elif key == "flash_atr_mult":
            if hasattr(self._store, "update_flash_crash_params"):
                self._store.update_flash_crash_params(atr_multiplier=float(value))
        elif key == "flash_pct":
            if hasattr(self._store, "update_flash_crash_params"):
                self._store.update_flash_crash_params(pct_threshold=float(value))
        elif key == "dynamic_reward_risk_enabled":
            if hasattr(self._store, "update_strategy_params"):
                self._store.update_strategy_params(dynamic_reward_risk_enabled=bool(value))
        elif key in ("reward_risk", "reward_risk_min", "reward_risk_max", "atr_stop_mult", "min_stop_cost_mult", "min_target_cost_mult"):
            if hasattr(self._store, "update_strategy_params"):
                kwargs: dict[str, Any] = {key: float(value)}
                self._store.update_strategy_params(**kwargs)
        elif key == "max_holding_min":
            if hasattr(self._store, "update_strategy_params"):
                self._store.update_strategy_params(max_holding_ms=int(value) * 60_000)
        return f"{key} = {value}"
