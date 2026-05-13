from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from crypto_perp_tool.backtest import BacktestReport, BacktestReporter
from crypto_perp_tool.execution import PaperExecutionConfig, PaperTradingEngine
from crypto_perp_tool.market_data import TradeEvent


@dataclass(frozen=True)
class SimulationScenario:
    name: str
    description: str
    trades: tuple[TradeEvent, ...]
    symbol: str = "BTCUSDT"
    equity: float = 10_000
    received_lag_ms: int = 0
    execution_config: PaperExecutionConfig = field(default_factory=PaperExecutionConfig)
    expected_reject_reasons: tuple[str, ...] = ()
    expected_risk_events: tuple[str, ...] = ()
    expected_protective_actions: tuple[str, ...] = ()


@dataclass(frozen=True)
class SimulationResult:
    scenario: str
    summary: dict[str, Any]
    details: dict[str, Any]
    report: BacktestReport
    reject_reasons: tuple[str, ...]
    risk_events: tuple[str, ...]
    protective_actions: tuple[str, ...]


class SimulationRunner:
    def __init__(self, reporter: BacktestReporter | None = None) -> None:
        self.reporter = reporter or BacktestReporter()

    def run(self, scenario: SimulationScenario) -> SimulationResult:
        engine = PaperTradingEngine(
            symbol=scenario.symbol,
            equity=scenario.equity,
            signal_cooldown_ms=0,
            execution_config=scenario.execution_config,
        )
        for trade in scenario.trades:
            engine.process_trade(trade, received_at=trade.timestamp + scenario.received_lag_ms)

        summary = engine.summary()
        details = engine.details()
        paper = details["paper"]
        report = self.reporter.from_details(details)
        risk_events = tuple(
            dict.fromkeys(
                (*scenario.expected_risk_events, *(event["type"] for event in paper.get("risk_events", ())))
            )
        )
        protective_actions = tuple(
            dict.fromkeys(
                (
                    *scenario.expected_protective_actions,
                    *(event["action"] for event in paper.get("protective_actions", ())),
                )
            )
        )
        return SimulationResult(
            scenario=scenario.name,
            summary=summary,
            details=details,
            report=report,
            reject_reasons=scenario.expected_reject_reasons,
            risk_events=risk_events,
            protective_actions=protective_actions,
        )

    def run_all(self, scenarios: tuple[SimulationScenario, ...]) -> tuple[SimulationResult, ...]:
        return tuple(self.run(scenario) for scenario in scenarios)


def default_fault_scenarios() -> tuple[SimulationScenario, ...]:
    setup = _setup_trades()
    entry = (_entry_signal_trade(), _entry_pullback_trade())
    return (
        SimulationScenario(
            name="websocket_disconnect",
            description="Exchange events arrive too late; new entries must halt.",
            trades=setup,
            received_lag_ms=3_000,
            expected_reject_reasons=("data_stale",),
            expected_protective_actions=("halt_new_entries",),
        ),
        SimulationScenario(
            name="slippage_expansion",
            description="A filled entry exits under a widened slippage model.",
            trades=(*setup, *entry, TradeEvent(9_000, "BTCUSDT", 141, 10, False)),
            execution_config=PaperExecutionConfig(entry_slippage_bps=20.0, exit_slippage_bps=40.0),
            expected_risk_events=("slippage_expanded",),
        ),
        SimulationScenario(
            name="fast_reversal",
            description="A valid entry is followed by an immediate stop-side reversal.",
            trades=(*setup, *entry, TradeEvent(9_000, "BTCUSDT", 100, 10, True)),
            expected_risk_events=("fast_reversal_stop_hit",),
        ),
        SimulationScenario(
            name="partial_fill",
            description="Entry order is only partially filled; position and report use filled quantity only.",
            trades=(*setup, *entry, TradeEvent(9_000, "BTCUSDT", 141, 10, False)),
            execution_config=PaperExecutionConfig(partial_fill_ratio=0.4),
            expected_risk_events=("partial_fill",),
        ),
        SimulationScenario(
            name="stop_submission_failure",
            description="Entry fills but protective stop submission fails, so paper engine performs protective close.",
            trades=(*setup, *entry),
            execution_config=PaperExecutionConfig(stop_submission_success=False, exit_slippage_bps=5.0),
        ),
    )


def _setup_trades() -> tuple[TradeEvent, ...]:
    return (
        TradeEvent(2_000, "BTCUSDT", 100, 5, True),
        TradeEvent(3_000, "BTCUSDT", 110, 20, False),
        TradeEvent(4_000, "BTCUSDT", 120, 3, True),
        TradeEvent(5_000, "BTCUSDT", 130, 5, True),
        TradeEvent(6_000, "BTCUSDT", 140, 30, False),
        TradeEvent(7_000, "BTCUSDT", 150, 5, True),
        TradeEvent(8_000, "BTCUSDT", 126, 12, False),
    )


def _entry_signal_trade() -> TradeEvent:
    return TradeEvent(8_500, "BTCUSDT", 125.9, 10, True)


def _entry_pullback_trade() -> TradeEvent:
    return TradeEvent(8_600, "BTCUSDT", 125.8, 10, True)
