from __future__ import annotations

import argparse
import json
from pathlib import Path

from crypto_perp_tool.config import default_settings
from crypto_perp_tool.journal import JsonlJournal
from crypto_perp_tool.paper import PaperRunner
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

    args = parser.parse_args(argv)

    if args.command == "config" and args.config_command == "show":
        print(json.dumps(to_jsonable(default_settings()), ensure_ascii=False, indent=2))
        return 0

    if args.command == "paper" and args.paper_command == "run":
        result = PaperRunner(equity=args.equity, journal_path=Path(args.journal)).run_csv(Path(args.csv))
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

    if args.command == "web" and args.web_command == "serve":
        serve_dashboard(
            host=normalize_bind_host(args.host, args.mobile),
            port=args.port,
            data_path=Path(args.csv),
            source=args.source,
            symbol=args.symbol,
            paper_journal_path=Path(args.paper_journal),
        )
        return 0

    parser.error("unsupported command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
