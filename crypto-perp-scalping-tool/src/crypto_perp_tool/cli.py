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
