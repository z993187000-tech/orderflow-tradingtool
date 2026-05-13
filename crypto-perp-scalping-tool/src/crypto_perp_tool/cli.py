from __future__ import annotations

import argparse
import json
from pathlib import Path

from crypto_perp_tool.config import default_settings
from crypto_perp_tool.journal import JsonlJournal, TradeLogger
from crypto_perp_tool.paper import PaperRunner
from crypto_perp_tool.replay import ReplayEngine
from crypto_perp_tool.risk import AccountState, RiskEngine
from crypto_perp_tool.serialization import to_jsonable
from crypto_perp_tool.simulation import SimulationRunner, default_fault_scenarios
from crypto_perp_tool.types import SignalSide, TradeSignal
from crypto_perp_tool.web.network import normalize_bind_host
from crypto_perp_tool.web.server import serve_dashboard


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="crypto-tool")
    subparsers = parser.add_subparsers(dest="command", required=True)

    config_parser = subparsers.add_parser("config")
    config_sub = config_parser.add_subparsers(dest="config_command", required=True)
    config_sub.add_parser("show")

    paper_parser = subparsers.add_parser("paper")
    paper_sub = paper_parser.add_subparsers(dest="paper_command", required=True)
    run_parser = paper_sub.add_parser("run")
    run_parser.add_argument("--csv", required=True)
    run_parser.add_argument("--journal", default="data/journal.jsonl")
    run_parser.add_argument("--trade-log", default="data/trade-log.jsonl")
    run_parser.add_argument("--equity", type=float, default=10_000)

    journal_parser = subparsers.add_parser("journal")
    journal_sub = journal_parser.add_subparsers(dest="journal_command", required=True)
    tail_parser = journal_sub.add_parser("tail")
    tail_parser.add_argument("--path", default="data/journal.jsonl")
    tail_parser.add_argument("--limit", type=int, default=20)

    risk_parser = subparsers.add_parser("risk")
    risk_sub = risk_parser.add_subparsers(dest="risk_command", required=True)
    check_parser = risk_sub.add_parser("check")
    check_parser.add_argument("--json", required=True)

    simulation_parser = subparsers.add_parser("simulation")
    simulation_sub = simulation_parser.add_subparsers(dest="simulation_command", required=True)
    simulation_sub.add_parser("run")

    trade_log_parser = subparsers.add_parser("trade-log")
    trade_log_sub = trade_log_parser.add_subparsers(dest="trade_log_command", required=True)
    tl_export = trade_log_sub.add_parser("export")
    tl_export.add_argument("--journal", required=True)
    tl_export.add_argument("--format", choices=("csv", "json"), default="csv")
    tl_export.add_argument("--output", required=True)
    tl_show = trade_log_sub.add_parser("show")
    tl_show.add_argument("--journal", required=True)
    tl_show.add_argument("--limit", type=int, default=20)

    replay_parser = subparsers.add_parser("replay")
    replay_sub = replay_parser.add_subparsers(dest="replay_command", required=True)
    replay_run = replay_sub.add_parser("run")
    replay_run.add_argument("--journal", required=True, help="Path to JSONL journal file")
    replay_run.add_argument("--csv", help="Path to CSV trade data for replay events")
    replay_run.add_argument("--symbol", default="BTCUSDT")
    replay_run.add_argument("--start", type=int, help="Start timestamp ms for time-range filter")
    replay_run.add_argument("--end", type=int, help="End timestamp ms for time-range filter")

    data_parser = subparsers.add_parser("data")
    data_sub = data_parser.add_subparsers(dest="data_command", required=True)
    download_parser = data_sub.add_parser("download")
    download_parser.add_argument("--symbol", default="BTCUSDT")
    download_parser.add_argument("--output", required=True, help="Path to output CSV file")
    download_parser.add_argument("--start", help="Start time in ISO format or timestamp ms")
    download_parser.add_argument("--end", help="End time in ISO format or timestamp ms")
    download_parser.add_argument("--pages", type=int, default=50, help="Max pagination pages (1000 trades each)")

    backtest_parser = subparsers.add_parser("backtest")
    backtest_sub = backtest_parser.add_subparsers(dest="backtest_command", required=True)
    backtest_run = backtest_sub.add_parser("run")
    backtest_run.add_argument("--csv", required=True, help="Path to CSV trade data")
    backtest_run.add_argument("--symbol", default="BTCUSDT")
    backtest_run.add_argument("--equity", type=float, default=10_000)
    backtest_run.add_argument("--entry-slippage", type=float, default=2.0, help="Entry slippage in bps")
    backtest_run.add_argument("--exit-slippage", type=float, default=3.0, help="Exit slippage in bps")
    backtest_run.add_argument("--fee", type=float, default=4.0, help="Taker fee in bps")
    backtest_run.add_argument("--split", type=float, default=0.0, help="IS fraction for walk-forward (0=disabled, 0.6=60%25 IS)")
    backtest_run.add_argument("--start", type=int, help="Start timestamp ms for time filter")
    backtest_run.add_argument("--end", type=int, help="End timestamp ms for time filter")

    web_parser = subparsers.add_parser("web")
    web_sub = web_parser.add_subparsers(dest="web_command", required=True)
    serve_parser = web_sub.add_parser("serve")
    serve_parser.add_argument("--source", choices=("csv", "binance"), default="csv")
    serve_parser.add_argument("--symbol", default="BTCUSDT")
    serve_parser.add_argument("--csv", default="data/sample_trades.csv")
    serve_parser.add_argument("--host", default=None)
    serve_parser.add_argument("--port", type=int, default=8000)
    serve_parser.add_argument("--paper-journal", default="data/live-paper.jsonl")
    serve_parser.add_argument("--mobile", action="store_true", help="Bind to all interfaces and print phone/LAN URLs.")
    serve_parser.add_argument("--testing", action="store_true", help="Disable circuit breaker and risk limits for testing.")

    args = parser.parse_args(argv)

    if args.command == "config" and args.config_command == "show":
        print(json.dumps(to_jsonable(default_settings()), ensure_ascii=False, indent=2))
        return 0

    if args.command == "paper" and args.paper_command == "run":
        result = PaperRunner(
            equity=args.equity,
            journal_path=Path(args.journal),
            trade_log_path=Path(args.trade_log),
        ).run_csv(Path(args.csv))
        print(json.dumps(to_jsonable(result), ensure_ascii=False, indent=2))
        return 0

    if args.command == "journal" and args.journal_command == "tail":
        events = JsonlJournal(Path(args.path)).tail(limit=args.limit)
        print(json.dumps(to_jsonable(events), ensure_ascii=False, indent=2))
        return 0

    if args.command == "data" and args.data_command == "download":
        import csv
        from crypto_perp_tool.market_data.binance import BinanceHistoricalAggTradeClient

        start_ms = _parse_time(args.start)
        end_ms = _parse_time(args.end)
        client = BinanceHistoricalAggTradeClient()
        trades = client.download(args.symbol, start_time=start_ms, end_time=end_ms, max_pages=args.pages)

        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["timestamp", "symbol", "price", "quantity", "is_buyer_maker"])
            writer.writeheader()
            for t in trades:
                writer.writerow({
                    "timestamp": t.timestamp, "symbol": t.symbol,
                    "price": t.price, "quantity": t.quantity,
                    "is_buyer_maker": t.is_buyer_maker,
                })
        print(f"Downloaded {len(trades)} aggTrades to {output}")
        return 0

    if args.command == "risk" and args.risk_command == "check":
        payload = json.loads(Path(args.json).read_text(encoding="utf-8"))
        signal_payload = payload["signal"]
        account_payload = payload["account"]
        signal = TradeSignal(
            id=signal_payload["id"],
            symbol=signal_payload["symbol"],
            side=SignalSide(signal_payload["side"]),
            setup=signal_payload["setup"],
            entry_price=float(signal_payload["entry_price"]),
            stop_price=float(signal_payload["stop_price"]),
            target_price=float(signal_payload["target_price"]),
            confidence=float(signal_payload["confidence"]),
            reasons=tuple(signal_payload.get("reasons", ())),
            invalidation_rules=tuple(signal_payload.get("invalidation_rules", ())),
            created_at=int(signal_payload["created_at"]),
        )
        account = AccountState(
            equity=float(account_payload["equity"]),
            realized_pnl_today=float(account_payload["realized_pnl_today"]),
            consecutive_losses=int(account_payload["consecutive_losses"]),
        )
        decision = RiskEngine(default_settings().risk).evaluate(signal, account)
        print(json.dumps(to_jsonable(decision), ensure_ascii=False, indent=2))
        return 0

    if args.command == "trade-log" and args.trade_log_command == "export":
        logger = TradeLogger(Path(args.journal))
        if args.format == "csv":
            count = logger.export_csv(Path(args.output))
            print(f"Exported {count} trade records to {args.output}")
        else:
            records = logger.read_all()
            with open(args.output, "w", encoding="utf-8") as f:
                json.dump(to_jsonable([r.to_csv_row() for r in records]), f, ensure_ascii=False, indent=2)
            print(f"Exported {len(records)} trade records to {args.output}")
        return 0

    if args.command == "trade-log" and args.trade_log_command == "show":
        logger = TradeLogger(Path(args.journal))
        records = logger.read_all()
        if args.limit:
            records = records[-args.limit:]
        for record in records:
            print(
                f"{record.trade_id} | {record.setup} | {record.side} | "
                f"entry {record.entry_price:.2f} exit {record.exit_price:.2f} | "
                f"net {record.net_pnl:.2f} ({record.pnl_percent:+.2f}%) | R={record.r_multiple:.2f} | "
                f"{record.exit_reason}"
            )
        print(f"\n{len(records)} trade record(s)")
        return 0

    if args.command == "simulation" and args.simulation_command == "run":
        results = SimulationRunner().run_all(default_fault_scenarios())
        payload = {
            "scenarios": len(results),
            "by_scenario": {
                result.scenario: {
                    "summary": result.summary,
                    "report": result.report,
                    "reject_reasons": result.reject_reasons,
                    "risk_events": result.risk_events,
                    "protective_actions": result.protective_actions,
                }
                for result in results
            },
        }
        print(json.dumps(to_jsonable(payload), ensure_ascii=False, indent=2))
        return 0

    if args.command == "backtest" and args.backtest_command == "run":
        from crypto_perp_tool.backtest import BacktestConfig, BacktestEngine
        events = BacktestEngine.load_csv(Path(args.csv), symbol=args.symbol)
        config = BacktestConfig(
            symbol=args.symbol,
            equity=args.equity,
            start_ms=args.start,
            end_ms=args.end,
            entry_slippage_bps=args.entry_slippage,
            exit_slippage_bps=args.exit_slippage,
            fee_bps=args.fee,
            is_fraction=args.split if args.split > 0 else 0.0,
            oos_fraction=(1.0 - args.split) if args.split > 0 else 0.0,
        )
        engine = BacktestEngine(config=config)
        if config.is_fraction > 0:
            is_result, oos_result = engine.run_split(events)
            print(json.dumps(to_jsonable({
                "symbol": args.symbol,
                "total_events": is_result.total_events + oos_result.total_events,
                "in_sample": _format_backtest_result(is_result),
                "out_of_sample": _format_backtest_result(oos_result),
                "walk_forward_efficiency": round(
                    (oos_result.report.profit_factor / is_result.report.profit_factor)
                    if is_result.report and is_result.report.profit_factor > 0 else 0.0, 4),
            }), ensure_ascii=False, indent=2))
        else:
            result = engine.run(events)
            print(json.dumps(to_jsonable({
                "symbol": result.symbol,
                "total_events": result.total_events,
                "equity_start": result.equity_start,
                "equity_end": result.equity_end,
                "total_return_pct": result.total_return_pct,
                "data_quality": result.data_quality,
                "config_version": result.config_version,
                "report": result.report,
                "trade_count": len(result.trade_records),
            }), ensure_ascii=False, indent=2))
        return 0

    if args.command == "replay" and args.replay_command == "run":
        if not Path(args.journal).exists():
            print(f"Error: journal file not found: {args.journal}")
            return 1
        engine = ReplayEngine(journal_path=Path(args.journal), symbol=args.symbol)
        if args.csv:
            from crypto_perp_tool.market_data import TradeEvent
            events = _load_replay_csv(Path(args.csv), args.symbol)
        else:
            events = []
        if not events:
            print("Warning: no CSV events provided; replay will compare journal signals only.")
        report = engine.replay(events, start_ms=args.start, end_ms=args.end)
        print(json.dumps(to_jsonable({
            "journal_path": report.journal_path,
            "symbol": report.symbol,
            "total_journal_signals": report.total_journal_signals,
            "replayed_signals": report.replayed_signals,
            "matched": report.matched,
            "missed": report.missed,
            "extra": report.extra,
            "match_rate": round(report.matched / max(report.total_journal_signals, 1), 4),
            "price_matched": report.price_matched,
            "avg_entry_diff_pct": report.avg_entry_diff_pct,
            "avg_stop_diff_pct": report.avg_stop_diff_pct,
            "avg_target_diff_pct": report.avg_target_diff_pct,
        }), ensure_ascii=False, indent=2))
        return 0

    if args.command == "web" and args.web_command == "serve":
        serve_dashboard(
            host=normalize_bind_host(args.host, args.mobile),
            port=args.port,
            data_path=Path(args.csv),
            source=args.source,
            symbol=args.symbol,
            paper_journal_path=Path(args.paper_journal),
            testing_mode=args.testing,
        )
        return 0

    parser.error("unsupported command")
    return 2


def _format_backtest_result(result) -> dict:
    report = result.report
    return {
        "equity_start": result.equity_start,
        "equity_end": result.equity_end,
        "total_return_pct": result.total_return_pct,
        "total_events": result.total_events,
        "total_trades": report.total_trades if report else 0,
        "win_rate": report.win_rate if report else 0.0,
        "profit_factor": report.profit_factor if report else 0.0,
        "net_pnl": report.net_pnl if report else 0.0,
        "average_r": report.average_r if report else 0.0,
        "max_drawdown": report.max_drawdown if report else 0.0,
        "max_consecutive_losses": report.max_consecutive_losses if report else 0,
        "errors": result.errors,
    }


def _parse_time(value: str | None) -> int | None:
    """Parse a timestamp argument: ISO format string or raw milliseconds integer."""
    if value is None:
        return None
    value = value.strip()
    if value.isdigit() or (value.startswith("-") and value[1:].isdigit()):
        return int(value)
    # Try ISO format like "2026-05-01T00:00:00" or "2026-05-01"
    from datetime import datetime, timezone
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(value, fmt)
            return int(dt.replace(tzinfo=timezone.utc).timestamp() * 1000)
        except ValueError:
            continue
    raise ValueError(f"Cannot parse time: {value}")


def _load_replay_csv(path: Path, symbol: str) -> list:
    import csv
    from crypto_perp_tool.market_data import TradeEvent
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = csv.DictReader(handle)
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


if __name__ == "__main__":
    raise SystemExit(main())
