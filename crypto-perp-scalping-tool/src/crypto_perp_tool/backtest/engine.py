from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from crypto_perp_tool.backtest.report import BacktestReport, BacktestReporter
from crypto_perp_tool.config import Settings, default_settings
from crypto_perp_tool.execution.paper_engine import PaperExecutionConfig, PaperTradingEngine
from crypto_perp_tool.market_data import QuoteEvent, TradeEvent
from crypto_perp_tool.types import TradeRecord, make_trade_record


@dataclass
class BacktestConfig:
    """Configuration for a single backtest run."""
    symbol: str = "BTCUSDT"
    equity: float = 10_000
    start_ms: int | None = None
    end_ms: int | None = None
    entry_slippage_bps: float = 2.0
    exit_slippage_bps: float = 3.0
    fee_bps: float = 4.0
    signal_cooldown_ms: int = 60_000

    # Walk-forward split (set both > 0 to enable)
    is_fraction: float = 0.0       # 0 = no split; 0.6 = 60% in-sample
    oos_fraction: float = 0.0      # must sum to 1.0 with is_fraction


@dataclass
class BacktestResult:
    """Output of a single backtest run."""
    symbol: str
    equity_start: float
    equity_end: float
    total_return_pct: float
    report: BacktestReport | None = None
    equity_curve: list[float] = field(default_factory=list)
    trade_records: list[TradeRecord] = field(default_factory=list)
    config_version: str = ""
    data_quality: str = "aggTrade"
    is_in_sample: bool = True
    total_events: int = 0
    errors: list[str] = field(default_factory=list)


class BacktestEngine:
    """Runs tick-by-tick backtests using PaperTradingEngine as the execution core.

    Feeds TradeEvent (and optional QuoteEvent) through the full pipeline:
    profile → signal → risk → execution, tracking equity at each step.
    """

    def __init__(self, config: BacktestConfig | None = None, settings: Settings | None = None) -> None:
        self.config = config or BacktestConfig()
        self.settings = settings or default_settings()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(
        self,
        events: list[TradeEvent],
        quotes: list[QuoteEvent] | None = None,
    ) -> BacktestResult:
        """Run a single backtest pass over sorted events."""
        filtered, quote_map = self._prepare(events, quotes)
        if not filtered:
            return self._empty_result("no events after time filter")

        taker_fee_rate = self.config.fee_bps / 10_000
        engine = PaperTradingEngine(
            symbol=self.config.symbol,
            equity=self.config.equity,
            signal_cooldown_ms=self.config.signal_cooldown_ms,
            execution_config=PaperExecutionConfig(
                entry_slippage_bps=self.config.entry_slippage_bps,
                exit_slippage_bps=self.config.exit_slippage_bps,
            ),
            taker_fee_rate=taker_fee_rate,
        )
        equity_curve: list[float] = [self.config.equity]

        for event in filtered:
            quote = quote_map.get(event.timestamp)
            engine.process_trade(event, quote, received_at=event.timestamp)
            equity_curve.append(self.config.equity + engine._realized_pnl)

        details = engine.details()
        reporter = BacktestReporter(
            initial_equity=self.config.equity,
            config_version=self.settings.config_version,
        )
        report = reporter.from_details(details)
        records = self._build_trade_records(details)

        return BacktestResult(
            symbol=self.config.symbol,
            equity_start=self.config.equity,
            equity_end=equity_curve[-1],
            total_return_pct=round((equity_curve[-1] - self.config.equity) / self.config.equity * 100, 4),
            report=report,
            equity_curve=equity_curve,
            trade_records=records,
            config_version=self.settings.config_version,
            data_quality="aggTrade" if quote_map else "aggTrade_no_quotes",
            total_events=len(filtered),
        )

    def run_split(
        self,
        events: list[TradeEvent],
        quotes: list[QuoteEvent] | None = None,
    ) -> tuple[BacktestResult, BacktestResult]:
        """Run in-sample / out-of-sample split backtest.

        Returns (is_result, oos_result). If split fractions are not set (both 0),
        raises ValueError.
        """
        is_frac = self.config.is_fraction
        oos_frac = self.config.oos_fraction
        if is_frac <= 0 or oos_frac <= 0:
            raise ValueError("is_fraction and oos_fraction must both be > 0 for split run")
        if abs(is_frac + oos_frac - 1.0) > 0.001:
            raise ValueError("is_fraction + oos_fraction must equal 1.0")

        sorted_events = sorted(events, key=lambda e: e.timestamp)
        if not sorted_events:
            raise ValueError("no events to split")

        quote_map: dict[int, QuoteEvent] = {}
        if quotes:
            for q in quotes:
                quote_map[q.timestamp] = q

        first_ts = sorted_events[0].timestamp
        last_ts = sorted_events[-1].timestamp
        total_duration = last_ts - first_ts
        is_cutoff = first_ts + int(total_duration * is_frac)

        is_events = [e for e in sorted_events if e.timestamp < is_cutoff]
        oos_events = [e for e in sorted_events if e.timestamp >= is_cutoff]
        is_quotes = [q for q in (quotes or []) if q.timestamp < is_cutoff]
        oos_quotes = [q for q in (quotes or []) if q.timestamp >= is_cutoff]

        is_result = self.run(is_events, is_quotes)
        is_result.is_in_sample = True

        oos_result = self.run(oos_events, oos_quotes)
        oos_result.is_in_sample = False

        return is_result, oos_result

    # ------------------------------------------------------------------
    # CSV loading
    # ------------------------------------------------------------------

    @staticmethod
    def load_csv(path: Path | str, symbol: str = "BTCUSDT") -> list[TradeEvent]:
        """Load TradeEvent rows from a CSV file.

        Required columns: timestamp, price, quantity
        Optional columns: symbol, is_buyer_maker
        """
        with Path(path).open("r", encoding="utf-8", newline="") as handle:
            rows = csv.DictReader(handle)
            fieldnames = set(rows.fieldnames or [])
            missing = sorted({"timestamp", "price", "quantity"} - fieldnames)
            if missing:
                raise ValueError(f"missing required columns: {', '.join(missing)}")
            return [
                TradeEvent(
                    timestamp=int(row["timestamp"]),
                    symbol=row.get("symbol", symbol),
                    price=float(row["price"]),
                    quantity=float(row["quantity"]),
                    is_buyer_maker=str(row.get("is_buyer_maker", "false")).lower() == "true",
                )
                for row in rows
            ]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _prepare(
        self, events: list[TradeEvent], quotes: list[QuoteEvent] | None
    ) -> tuple[list[TradeEvent], dict[int, QuoteEvent]]:
        quote_map: dict[int, QuoteEvent] = {}
        if quotes:
            for q in quotes:
                if q.symbol.upper() == self.config.symbol:
                    quote_map[q.timestamp] = q

        filtered = sorted(
            [e for e in events if e.symbol.upper() == self.config.symbol],
            key=lambda e: e.timestamp,
        )
        if self.config.start_ms is not None:
            filtered = [e for e in filtered if e.timestamp >= self.config.start_ms]
        if self.config.end_ms is not None:
            filtered = [e for e in filtered if e.timestamp <= self.config.end_ms]

        return filtered, quote_map

    def _build_trade_records(self, details: dict[str, Any]) -> list[TradeRecord]:
        paper = details.get("paper", {})
        records: list[TradeRecord] = []
        for closed in paper.get("closed_positions", []):
            try:
                records.append(make_trade_record(
                    signal_id=str(closed.get("signal_id", "")),
                    setup=str(closed.get("setup", "unknown")),
                    symbol=str(closed.get("symbol", self.config.symbol)),
                    side=str(closed.get("side", "")),
                    entry_time=int(closed.get("opened_at", closed.get("timestamp", 0))),
                    entry_price=float(closed.get("entry_price", 0)),
                    quantity=float(closed.get("quantity", 0)),
                    entry_fee=float(closed.get("entry_fee", 0)),
                    signal_entry_price=float(closed.get("signal_entry_price", closed.get("entry_price", 0))),
                    initial_stop_price=float(closed.get("initial_stop_price", closed.get("stop_price", 0))),
                    stop_price=float(closed.get("stop_price", 0)),
                    target_price=float(closed.get("target_price", 0)),
                    exit_time=int(closed.get("timestamp", 0)),
                    exit_price=float(closed.get("close_price", 0)),
                    exit_reason=str(closed.get("exit_reason", "unknown")),
                    exit_fee=float(closed.get("close_fee", closed.get("fee", 0))),
                    gross_pnl=float(closed.get("gross_realized_pnl", closed.get("realized_pnl", 0))),
                    net_pnl=float(closed.get("net_realized_pnl", closed.get("realized_pnl", 0))),
                    break_even_shifted=bool(closed.get("break_even_shifted", False)),
                    absorption_reduced=bool(closed.get("absorption_reduced", False)),
                    max_favorable_move=float(closed.get("max_favorable_move", 0)),
                    max_adverse_move=float(closed.get("max_adverse_move", 0)),
                ))
            except (ValueError, TypeError, KeyError):
                continue
        return records

    def _empty_result(self, error: str) -> BacktestResult:
        return BacktestResult(
            symbol=self.config.symbol,
            equity_start=self.config.equity,
            equity_end=self.config.equity,
            total_return_pct=0.0,
            errors=[error],
        )
