from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from crypto_perp_tool.security import redact
from crypto_perp_tool.serialization import to_jsonable


class JsonlJournal:
    def __init__(self, path: Path | str, config_version: str = "") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._config_version = config_version

    def write(self, event_type: str, payload: dict[str, Any]) -> None:
        event = {
            "type": event_type,
            "time": int(time.time() * 1000),
            "payload": redact(to_jsonable(payload)),
        }
        if self._config_version:
            event["config_version"] = self._config_version
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")

    def tail(self, limit: int = 20) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines[-limit:]]
