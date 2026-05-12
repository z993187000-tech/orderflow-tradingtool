from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from crypto_perp_tool.serialization import to_jsonable
from crypto_perp_tool.types import TradeRecord


class TradeLogger:
    """Persists TradeRecord entries as JSONL and supports CSV export."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._seen_ids: set[str] = self._load_existing_ids()

    def _load_existing_ids(self) -> set[str]:
        if not self.path.exists():
            return set()
        ids: set[str] = set()
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "trade_record":
                payload = event.get("payload", {})
                if isinstance(payload, dict) and "trade_id" in payload:
                    ids.add(payload["trade_id"])
        return ids

    def write(self, record: TradeRecord) -> bool:
        if record.trade_id in self._seen_ids:
            return False
        self._seen_ids.add(record.trade_id)
        event = {"type": "trade_record", "time": record.exit_time, "payload": to_jsonable(record)}
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        return True

    def read_all(self) -> list[TradeRecord]:
        if not self.path.exists():
            return []
        records: list[TradeRecord] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") != "trade_record":
                continue
            records.append(_record_from_payload(event["payload"]))
        return records

    def export_csv(self, output_path: Path | str) -> int:
        records = self.read_all()
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        headers = TradeRecord.csv_headers()
        with output_path.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=headers, extrasaction="ignore")
            writer.writeheader()
            for record in records:
                writer.writerow(record.to_csv_row())
        return len(records)


def _record_from_payload(payload: dict[str, Any]) -> TradeRecord:
    return TradeRecord(
        trade_id=payload["trade_id"],
        signal_id=payload["signal_id"],
        setup=payload["setup"],
        symbol=payload["symbol"],
        side=payload["side"],
        entry_time=int(payload["entry_time"]),
        entry_price=float(payload["entry_price"]),
        quantity=float(payload["quantity"]),
        entry_fee=float(payload["entry_fee"]),
        signal_entry_price=float(payload["signal_entry_price"]),
        initial_stop_price=float(payload["initial_stop_price"]),
        stop_price=float(payload["stop_price"]),
        target_price=float(payload["target_price"]),
        exit_time=int(payload["exit_time"]),
        exit_price=float(payload["exit_price"]),
        exit_reason=payload["exit_reason"],
        exit_fee=float(payload["exit_fee"]),
        gross_pnl=float(payload["gross_pnl"]),
        net_pnl=float(payload["net_pnl"]),
        pnl_percent=float(payload["pnl_percent"]),
        holding_time_ms=int(payload["holding_time_ms"]),
        r_multiple=float(payload["r_multiple"]),
        break_even_shifted=bool(payload["break_even_shifted"]),
        absorption_reduced=bool(payload["absorption_reduced"]),
        max_favorable_move=float(payload["max_favorable_move"]),
        max_adverse_move=float(payload["max_adverse_move"]),
        entry_session=payload.get("entry_session", "unknown"),
        vwap_at_entry=float(payload.get("vwap_at_entry", 0)),
        atr_at_entry=float(payload.get("atr_at_entry", 0)),
        spread_bps_at_entry=float(payload.get("spread_bps_at_entry", 0)),
        poc_at_entry=float(payload.get("poc_at_entry", 0)),
        vah_at_entry=float(payload.get("vah_at_entry", 0)),
        val_at_entry=float(payload.get("val_at_entry", 0)),
    )
