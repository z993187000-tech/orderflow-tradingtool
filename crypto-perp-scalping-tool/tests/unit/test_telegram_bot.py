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
                        "circuit_reason": "websocket_stale",
                        "cooldown_until": 1700000000000,
                    }
                }
        with tempfile.TemporaryDirectory() as tmp:
            service = TradingService(journal=JsonlJournal(Path(tmp) / "journal.jsonl"))
            handler = TelegramCommandHandler(service, allowed_chat_ids={123}, store=FakeLiveStore())

            response = handler.handle(chat_id=123, text="/circuit")

        self.assertIn("tripped", response)
        self.assertIn("websocket_stale", response)

    def test_circuit_without_store_returns_not_connected(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = TradingService(journal=JsonlJournal(Path(tmp) / "journal.jsonl"))
            handler = TelegramCommandHandler(service, allowed_chat_ids={123})

            response = handler.handle(chat_id=123, text="/circuit")

        self.assertIn("not connected", response.lower())

    def test_set_updates_risk_setting(self):
        class FakeLiveStore:
            def __init__(self):
                self.risk_updated = None
                self.equity_updated = None
                self.cooldown_updated = None
                self.flash_atr = None
                self.flash_pct = None
            def update_risk_settings(self, risk):
                self.risk_updated = risk
            def update_equity(self, value):
                self.equity_updated = value
            def update_circuit_cooldown(self, value):
                self.cooldown_updated = value
            def update_flash_crash_params(self, atr_multiplier=None, pct_threshold=None):
                if atr_multiplier is not None:
                    self.flash_atr = atr_multiplier
                if pct_threshold is not None:
                    self.flash_pct = pct_threshold

        with tempfile.TemporaryDirectory() as tmp:
            service = TradingService(journal=JsonlJournal(Path(tmp) / "journal.jsonl"))
            store = FakeLiveStore()
            service.set_store(store)
            handler = TelegramCommandHandler(service, allowed_chat_ids={123}, store=store)

            response = handler.handle(chat_id=123, text="/set max_leverage 5")

        self.assertIn("max_leverage = 5", response)
        self.assertIsNotNone(store.risk_updated)
        self.assertEqual(store.risk_updated.max_leverage, 5)

    def test_set_updates_equity_on_store(self):
        class FakeLiveStore:
            def __init__(self):
                self.equity_updated = None
                self.risk_updated = None
            def update_risk_settings(self, risk):
                self.risk_updated = risk
            def update_equity(self, value):
                self.equity_updated = value

        with tempfile.TemporaryDirectory() as tmp:
            service = TradingService(journal=JsonlJournal(Path(tmp) / "journal.jsonl"))
            store = FakeLiveStore()
            service.set_store(store)
            handler = TelegramCommandHandler(service, allowed_chat_ids={123}, store=store)

            response = handler.handle(chat_id=123, text="/set equity 25000")

        self.assertIn("equity = 25000", response)
        self.assertEqual(store.equity_updated, 25000)

    def test_set_without_store_returns_not_connected(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = TradingService(journal=JsonlJournal(Path(tmp) / "journal.jsonl"))
            handler = TelegramCommandHandler(service, allowed_chat_ids={123})

            response = handler.handle(chat_id=123, text="/set equity 25000")

        self.assertIn("not connected", response.lower())

    def test_set_invalid_key_returns_error(self):
        class FakeLiveStore:
            def update_risk_settings(self, risk): pass
            def update_equity(self, value): pass
            def update_circuit_cooldown(self, value): pass
            def update_flash_crash_params(self, atr_multiplier=None, pct_threshold=None): pass

        with tempfile.TemporaryDirectory() as tmp:
            service = TradingService(journal=JsonlJournal(Path(tmp) / "journal.jsonl"))
            store = FakeLiveStore()
            service.set_store(store)
            handler = TelegramCommandHandler(service, allowed_chat_ids={123}, store=store)

            response = handler.handle(chat_id=123, text="/set invalid_key 123")

        self.assertIn("unknown setting", response.lower())

    def test_set_value_out_of_range_returns_error(self):
        class FakeLiveStore:
            def update_risk_settings(self, risk): pass
            def update_equity(self, value): pass
            def update_circuit_cooldown(self, value): pass
            def update_flash_crash_params(self, atr_multiplier=None, pct_threshold=None): pass

        with tempfile.TemporaryDirectory() as tmp:
            service = TradingService(journal=JsonlJournal(Path(tmp) / "journal.jsonl"))
            store = FakeLiveStore()
            service.set_store(store)
            handler = TelegramCommandHandler(service, allowed_chat_ids={123}, store=store)

            response = handler.handle(chat_id=123, text="/set risk_per_trade 1.0")

        self.assertIn("must be", response.lower())

    def test_set_usage_when_missing_args(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = TradingService(journal=JsonlJournal(Path(tmp) / "journal.jsonl"))
            handler = TelegramCommandHandler(service, allowed_chat_ids={123})

            response = handler.handle(chat_id=123, text="/set")

        self.assertIn("usage", response.lower())

    def test_config_shows_risk_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = TradingService(journal=JsonlJournal(Path(tmp) / "journal.jsonl"))
            handler = TelegramCommandHandler(service, allowed_chat_ids={123})

            response = handler.handle(chat_id=123, text="/config")

        self.assertIn("risk_per_trade", response)
        self.assertIn("max_leverage", response)

    def test_set_updates_circuit_cooldown(self):
        class FakeLiveStore:
            def __init__(self):
                self.cooldown_updated = None
                self.risk_updated = None
            def update_risk_settings(self, risk):
                self.risk_updated = risk
            def update_circuit_cooldown(self, value):
                self.cooldown_updated = value

        with tempfile.TemporaryDirectory() as tmp:
            service = TradingService(journal=JsonlJournal(Path(tmp) / "journal.jsonl"))
            store = FakeLiveStore()
            service.set_store(store)
            handler = TelegramCommandHandler(service, allowed_chat_ids={123}, store=store)

            response = handler.handle(chat_id=123, text="/set cooldown_ms 300000")

        self.assertIn("cooldown_ms = 300000", response)
        self.assertEqual(store.cooldown_updated, 300000)

    def test_set_updates_flash_crash_params(self):
        class FakeLiveStore:
            def __init__(self):
                self.flash_atr = None
                self.flash_pct = None
                self.risk_updated = None
            def update_risk_settings(self, risk):
                self.risk_updated = risk
            def update_flash_crash_params(self, atr_multiplier=None, pct_threshold=None):
                if atr_multiplier is not None:
                    self.flash_atr = atr_multiplier
                if pct_threshold is not None:
                    self.flash_pct = pct_threshold

        with tempfile.TemporaryDirectory() as tmp:
            service = TradingService(journal=JsonlJournal(Path(tmp) / "journal.jsonl"))
            store = FakeLiveStore()
            service.set_store(store)
            handler = TelegramCommandHandler(service, allowed_chat_ids={123}, store=store)

            response1 = handler.handle(chat_id=123, text="/set flash_atr_mult 3.0")
            response2 = handler.handle(chat_id=123, text="/set flash_pct 0.02")

        self.assertIn("flash_atr_mult = 3.0", response1)
        self.assertIn("flash_pct = 0.02", response2)
        self.assertEqual(store.flash_atr, 3.0)
        self.assertEqual(store.flash_pct, 0.02)


if __name__ == "__main__":
    unittest.main()
