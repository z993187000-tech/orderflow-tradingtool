from __future__ import annotations

from dataclasses import asdict

from crypto_perp_tool.config import Settings, default_settings
from crypto_perp_tool.journal import JsonlJournal


class TradingService:
    def __init__(self, journal: JsonlJournal, settings: Settings | None = None) -> None:
        self.settings = settings or default_settings()
        self.journal = journal
        self.paused = False

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
