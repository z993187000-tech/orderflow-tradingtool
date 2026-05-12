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

    def test_positions_without_store_returns_not_connected(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = TradingService(journal=JsonlJournal(Path(tmp) / "journal.jsonl"))
            handler = TelegramCommandHandler(service, allowed_chat_ids={123})

            response = handler.handle(chat_id=123, text="/positions")

        self.assertIn("not connected", response.lower())

    def test_positions_returns_open_position_details(self):
        class FakeLiveStore:
            def view(self):
                return {
                    "summary": {
                        "open_position": {
                            "symbol": "BTCUSDT", "side": "long", "setup": "lvn_break_acceptance",
                            "entry_price": 96000.0, "stop_price": 95800.0, "target_price": 96500.0,
                            "quantity": 0.01, "break_even_shifted": False, "absorption_reduced": False,
                        }
                    }
                }
        with tempfile.TemporaryDirectory() as tmp:
            service = TradingService(journal=JsonlJournal(Path(tmp) / "journal.jsonl"))
            handler = TelegramCommandHandler(service, allowed_chat_ids={123}, store=FakeLiveStore())

            response = handler.handle(chat_id=123, text="/positions")

        self.assertIn("BTCUSDT", response)
        self.assertIn("long", response)
        self.assertIn("96000", response)
        self.assertIn("95800", response)
        self.assertIn("96500", response)

    def test_positions_returns_no_position_when_flat(self):
        class FakeLiveStore:
            def view(self):
                return {
                    "summary": {"open_position": None},
                    "details": {"paper": {"pnl_by_range": {"24h": 15.5}, "closed_positions": [{}]}},
                }
        with tempfile.TemporaryDirectory() as tmp:
            service = TradingService(journal=JsonlJournal(Path(tmp) / "journal.jsonl"))
            handler = TelegramCommandHandler(service, allowed_chat_ids={123}, store=FakeLiveStore())

            response = handler.handle(chat_id=123, text="/positions")

        self.assertIn("No open position", response)

    def test_circuit_returns_state(self):
        class FakeLiveStore:
            def view(self):
                return {
                    "summary": {
                        "circuit_state": "tripped",
                        "circuit_reason": "daily_loss_limit_reached",
                        "cooldown_until": 1700000000000,
                    }
                }
        with tempfile.TemporaryDirectory() as tmp:
            service = TradingService(journal=JsonlJournal(Path(tmp) / "journal.jsonl"))
            handler = TelegramCommandHandler(service, allowed_chat_ids={123}, store=FakeLiveStore())

            response = handler.handle(chat_id=123, text="/circuit")

        self.assertIn("tripped", response)
        self.assertIn("daily_loss_limit_reached", response)

    def test_circuit_without_store_returns_not_connected(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = TradingService(journal=JsonlJournal(Path(tmp) / "journal.jsonl"))
            handler = TelegramCommandHandler(service, allowed_chat_ids={123})

            response = handler.handle(chat_id=123, text="/circuit")

        self.assertIn("not connected", response.lower())


if __name__ == "__main__":
    unittest.main()
