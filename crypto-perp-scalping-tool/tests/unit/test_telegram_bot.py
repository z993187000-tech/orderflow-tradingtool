import tempfile
import unittest
from pathlib import Path

from crypto_perp_tool.journal import JsonlJournal
from crypto_perp_tool.service import TradingService
from crypto_perp_tool.telegram_bot import TelegramCommandHandler


class TelegramCommandHandlerTests(unittest.TestCase):
    def test_rejects_unauthorized_chat_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = TradingService(journal=JsonlJournal(Path(tmp) / "journal.jsonl"))
            handler = TelegramCommandHandler(service, allowed_chat_ids={123})

            response = handler.handle(chat_id=999, text="/status")

        self.assertIn("unauthorized", response.lower())

    def test_status_command_returns_paper_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = TradingService(journal=JsonlJournal(Path(tmp) / "journal.jsonl"))
            handler = TelegramCommandHandler(service, allowed_chat_ids={123})

            response = handler.handle(chat_id=123, text="/status")

        self.assertIn("mode=paper", response)

    def test_pause_and_resume_commands_update_service_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = TradingService(journal=JsonlJournal(Path(tmp) / "journal.jsonl"))
            handler = TelegramCommandHandler(service, allowed_chat_ids={123})

            pause_response = handler.handle(chat_id=123, text="/pause")
            paused_status = handler.handle(chat_id=123, text="/status")
            resume_response = handler.handle(chat_id=123, text="/resume")
            resumed_status = handler.handle(chat_id=123, text="/status")

        self.assertIn("paused", pause_response)
        self.assertIn("paused=true", paused_status)
        self.assertIn("resumed", resume_response)
        self.assertIn("paused=false", resumed_status)


if __name__ == "__main__":
    unittest.main()
