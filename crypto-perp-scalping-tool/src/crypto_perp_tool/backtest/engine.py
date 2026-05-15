from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from crypto_perp_tool.backtest.report import BacktestReport, BacktestReporter
from crypto_perp_tool.config import Settings, default_settings
from crypto_perp_tool.execution.paper_engine import PaperExecutionConfig, PaperTradingEngine
from crypto_perp_tool.market_data import KlineEvent, QuoteEvent, TradeEvent
from crypto_perp_tool.types import TradeRecord, make_trade_record


DEFAULT_KLINE_INTERVAL = "1m"
DEFAULT_KLINE_MS = 60_000


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
    data_quality: str = "kline_1m"
    is_in_sample: bool = True
    total_events: int = 0
    errors: list[str] = field(default_factory=list)


class BacktestEngine:
    """Runs Kline-based backtests using PaperTradingEngine as the execution core.

    Feeds one representative event per Kline through the full pipeline:
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
        events: list[TradeEvent | KlineEvent],
        quotes: list[QuoteEvent] | None = None,
    ) -> BacktestResult:
        """Run a single backtest pass over sorted Klines."""
        klines, quote_map = self._prepare_klines(events, quotes)
        if not klines:
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

        for kline in klines:
            event = self._trade_from_kline(kline)
            quote = quote_map.get(event.timestamp) or quote_map.get(kline.timestamp)
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
            data_quality=self._kline_data_quality(klines),
            total_events=len(klines),
        )

    def run_split(
        self,
        events: list[TradeEvent | KlineEvent],
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

        sorted_klines, _ = self._prepare_klines(events, quotes)
        if not sorted_klines:
            raise ValueError("no events to split")

        first_ts = sorted_klines[0].timestamp
        last_ts = sorted_klines[-1].timestamp
        total_duration = last_ts - first_ts
        is_cutoff = first_ts + int(total_duration * is_frac)

        is_events = [e for e in sorted_klines if e.timestamp < is_cutoff]
        oos_events = [e for e in sorted_klines if e.timestamp >= is_cutoff]
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
    def load_csv(path: Path | str, symbol: str = "BTCUSDT") -> list[KlineEvent]:
        """Load KlineEvent rows from a CSV file.

        OHLCV columns are loaded directly. AggTrade-style rows with
        timestamp/price/quantity are streamed into 1m Klines.
        """
        with Path(path).open("r", encoding="utf-8", newline="") as handle:
            rows = csv.DictReader(handle)
            fieldnames = set(rows.fieldnames or [])
            kline_columns = {"timestamp", "open", "high", "low", "close", "volume"}
            trade_columns = {"timestamp", "price", "quantity"}
            if kline_columns <= fieldnames:
                return [BacktestEngine._kline_from_row(row, symbol) for row in rows]
            if trade_columns <= fieldnames:
                return BacktestEngine._aggregate_trade_rows_to_klines(rows, symbol)
            missing = sorted(trade_columns - fieldnames)
            raise ValueError(f"missing required columns: {', '.join(missing)}")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _prepare_klines(
        self, events: list[TradeEvent | KlineEvent], quotes: list[QuoteEvent] | None
    ) -> tuple[list[KlineEvent], dict[int, QuoteEvent]]:
        quote_map: dict[int, QuoteEvent] = {}
        if quotes:
            for q in quotes:
                if q.symbol.upper() == self.config.symbol:
                    quote_map[q.timestamp] = q

        klines = self._normalize_to_klines(events)
        filtered = sorted([e for e in klines if e.symbol.upper() == self.config.symbol], key=lambda e: e.timestamp)
        if self.config.start_ms is not None:
            filtered = [e for e in filtered if e.timestamp >= self.config.start_ms]
        if self.config.end_ms is not None:
            filtered = [e for e in filtered if e.timestamp <= self.config.end_ms]

        return filtered, quote_map

    @classmethod
    def _normalize_to_klines(cls, events: list[TradeEvent | KlineEvent]) -> list[KlineEvent]:
        direct_klines: list[KlineEvent] = []
        trade_events: list[TradeEvent] = []
        for event in events:
            if isinstance(event, KlineEvent):
                direct_klines.append(event)
            elif isinstance(event, TradeEvent):
                trade_events.append(event)

        if trade_events:
            direct_klines.extend(cls._aggregate_trade_events_to_klines(trade_events))
        return sorted(direct_klines, key=lambda e: (e.timestamp, e.symbol))

    @staticmethod
    def _aggregate_trade_events_to_klines(events: list[TradeEvent]) -> list[KlineEvent]:
        buckets: dict[tuple[str, int], dict[str, Any]] = {}
        for event in events:
            symbol = event.symbol.upper()
            bucket_start = (event.timestamp // DEFAULT_KLINE_MS) * DEFAULT_KLINE_MS
            key = (symbol, bucket_start)
            bucket = buckets.get(key)
            if bucket is None:
                buckets[key] = {
                    "symbol": symbol,
                    "timestamp": bucket_start,
                    "first_ts": event.timestamp,
                    "last_ts": event.timestamp,
                    "open": event.price,
                    "high": event.price,
                    "low": event.price,
                    "close": event.price,
                    "volume": event.quantity,
                    "quote_volume": event.price * event.quantity,
                    "trade_count": 1,
                }
                continue

            if event.timestamp < bucket["first_ts"]:
                bucket["first_ts"] = event.timestamp
                bucket["open"] = event.price
            if event.timestamp >= bucket["last_ts"]:
                bucket["last_ts"] = event.timestamp
                bucket["close"] = event.price
            bucket["high"] = max(bucket["high"], event.price)
            bucket["low"] = min(bucket["low"], event.price)
            bucket["volume"] += event.quantity
            bucket["quote_volume"] += event.price * event.quantity
            bucket["trade_count"] += 1

        return [
            BacktestEngine._bucket_to_kline(bucket)
            for bucket in sorted(buckets.values(), key=lambda b: (b["timestamp"], b["symbol"]))
        ]

    @staticmethod
    def _aggregate_trade_rows_to_klines(rows: csv.DictReader, default_symbol: str) -> list[KlineEvent]:
        buckets: dict[tuple[str, int], dict[str, Any]] = {}
        for row in rows:
            timestamp = int(row["timestamp"])
            price = float(row["price"])
            quantity = float(row["quantity"])
            symbol = (row.get("symbol") or default_symbol).upper()
            bucket_start = (timestamp // DEFAULT_KLINE_MS) * DEFAULT_KLINE_MS
            key = (symbol, bucket_start)
            bucket = buckets.get(key)
            if bucket is None:
                buckets[key] = {
                    "symbol": symbol,
                    "timestamp": bucket_start,
                    "first_ts": timestamp,
                    "last_ts": timestamp,
                    "open": price,
                    "high": price,
                    "low": price,
                    "close": price,
                    "volume": quantity,
                    "quote_volume": price * quantity,
                    "trade_count": 1,
                }
                continue

            if timestamp < bucket["first_ts"]:
                bucket["first_ts"] = timestamp
                bucket["open"] = price
            if timestamp >= bucket["last_ts"]:
                bucket["last_ts"] = timestamp
                bucket["close"] = price
            bucket["high"] = max(bucket["high"], price)
            bucket["low"] = min(bucket["low"], price)
            bucket["volume"] += quantity
            bucket["quote_volume"] += price * quantity
            bucket["trade_count"] += 1

        return [
            BacktestEngine._bucket_to_kline(bucket)
            for bucket in sorted(buckets.values(), key=lambda b: (b["timestamp"], b["symbol"]))
        ]

    @staticmethod
    def _bucket_to_kline(bucket: dict[str, Any]) -> KlineEvent:
        timestamp = int(bucket["timestamp"])
        return KlineEvent(
            timestamp=timestamp,
            close_time=timestamp + DEFAULT_KLINE_MS - 1,
            symbol=str(bucket["symbol"]).upper(),
            interval=DEFAULT_KLINE_INTERVAL,
            open=float(bucket["open"]),
            high=float(bucket["high"]),
            low=float(bucket["low"]),
            close=float(bucket["close"]),
            volume=float(bucket["volume"]),
            quote_volume=float(bucket["quote_volume"]),
            trade_count=int(bucket["trade_count"]),
            is_closed=True,
        )

    @staticmethod
    def _kline_from_row(row: dict[str, str], default_symbol: str) -> KlineEvent:
        timestamp = int(row["timestamp"])
        interval = row.get("interval") or DEFAULT_KLINE_INTERVAL
        interval_ms = BacktestEngine._interval_to_ms(interval)
        close = float(row["close"])
        volume = float(row["volume"])
        return KlineEvent(
            timestamp=timestamp,
            close_time=int(row["close_time"]) if row.get("close_time") else timestamp + interval_ms - 1,
            symbol=(row.get("symbol") or default_symbol).upper(),
            interval=interval,
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=close,
            volume=volume,
            quote_volume=float(row["quote_volume"]) if row.get("quote_volume") else close * volume,
            trade_count=int(row["trade_count"]) if row.get("trade_count") else 1,
            is_closed=BacktestEngine._row_bool(row.get("is_closed"), default=True),
        )

    @staticmethod
    def _trade_from_kline(kline: KlineEvent) -> TradeEvent:
        return TradeEvent(
            timestamp=kline.close_time,
            symbol=kline.symbol,
            price=kline.close,
            quantity=max(kline.volume, 0.0),
            is_buyer_maker=kline.close < kline.open,
            exchange_event_time=kline.close_time,
        )

    @staticmethod
    def _kline_data_quality(klines: list[KlineEvent]) -> str:
        intervals = {kline.interval for kline in klines}
        if len(intervals) == 1:
            return f"kline_{next(iter(intervals))}"
        return "kline_mixed"

    @staticmethod
    def _interval_to_ms(interval: str) -> int:
        unit = interval[-1:]
        try:
            value = int(interval[:-1])
        except ValueError:
            return DEFAULT_KLINE_MS
        if unit == "m":
            return value * 60_000
        if unit == "h":
            return value * 60 * 60_000
        if unit == "d":
            return value * 24 * 60 * 60_000
        return DEFAULT_KLINE_MS

    @staticmethod
    def _row_bool(value: str | None, default: bool) -> bool:
        if value is None or value == "":
            return default
        return str(value).strip().lower() in {"1", "true", "yes", "y"}

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
