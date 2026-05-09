from __future__ import annotations

from crypto_perp_tool.service import TradingService


class TelegramCommandHandler:
    def __init__(self, service: TradingService, allowed_chat_ids: set[int]) -> None:
        self.service = service
        self.allowed_chat_ids = allowed_chat_ids

    def handle(self, chat_id: int, text: str) -> str:
        if chat_id not in self.allowed_chat_ids:
            self.service.journal.write("telegram_command_rejected", {"chat_id": chat_id, "text": text})
            return "unauthorized chat id"

        command = text.strip().split()[0].lower() if text.strip() else ""
        self.service.journal.write("telegram_command", {"chat_id": chat_id, "text": command})

        if command == "/status":
            return self.service.status()
        if command == "/pause":
            return self.service.pause(actor=f"telegram:{chat_id}")
        if command == "/resume":
            return self.service.resume(actor=f"telegram:{chat_id}")
        if command == "/risk":
            risk = self.service.risk()
            return " ".join(f"{key}={value}" for key, value in risk.items())
        if command == "/journal":
            return str(self.service.recent_journal(limit=3))
        return "unknown command"
