from dataclasses import dataclass

from crypto_perp_tool.config import RiskSettings
from crypto_perp_tool.types import RiskDecision, TradeSignal


@dataclass(frozen=True)
class AccountState:
    equity: float
    realized_pnl_today: float
    consecutive_losses: int


class RiskEngine:
    def __init__(self, settings: RiskSettings) -> None:
        self.settings = settings

    def evaluate(self, signal: TradeSignal, account: AccountState) -> RiskDecision:
        reject_reasons = list(self._account_reject_reasons(account))
        stop_distance = abs(signal.entry_price - signal.stop_price)
        if stop_distance <= 0:
            reject_reasons.append("invalid_stop_distance")

        quantity = 0.0
        if not reject_reasons:
            quantity = self._quantity(signal.entry_price, stop_distance, account.equity)
            if quantity <= 0:
                reject_reasons.append("quantity_below_minimum")

        return RiskDecision(
            signal_id=signal.id,
            allowed=not reject_reasons,
            quantity=quantity,
            max_slippage_bps=self._max_slippage_bps(signal.symbol),
            remaining_daily_risk=self._remaining_daily_risk(account),
            reject_reasons=tuple(reject_reasons),
        )

    def _quantity(self, entry_price: float, stop_distance: float, equity: float) -> float:
        risk_amount = equity * self.settings.risk_per_trade
        raw_quantity = risk_amount / stop_distance
        max_notional = equity * self.settings.max_symbol_notional_equity_multiple
        max_quantity = max_notional / entry_price
        return min(raw_quantity, max_quantity)

    def _account_reject_reasons(self, account: AccountState) -> tuple[str, ...]:
        reasons: list[str] = []
        daily_loss_amount = account.equity * self.settings.daily_loss_limit
        if account.realized_pnl_today <= -daily_loss_amount:
            reasons.append("daily_loss_limit_reached")
        if account.consecutive_losses >= self.settings.max_consecutive_losses:
            reasons.append("max_consecutive_losses_reached")
        return tuple(reasons)

    def _remaining_daily_risk(self, account: AccountState) -> float:
        daily_loss_amount = account.equity * self.settings.daily_loss_limit
        return max(0.0, daily_loss_amount + account.realized_pnl_today)

    def _max_slippage_bps(self, symbol: str) -> float:
        if symbol == "BTCUSDT":
            return 3
        if symbol == "ETHUSDT":
            return 4
        return 0
