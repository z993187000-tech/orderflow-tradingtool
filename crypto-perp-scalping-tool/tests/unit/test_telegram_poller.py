import tempfile
import unittest
from pathlib import Path
from unittest import mock

from crypto_perp_tool.journal import JsonlJournal
from crypto_perp_tool.service import TradingService
from crypto_perp_tool.telegram_bot import TelegramPoller, parse_allowed_chat_ids


class TelegramPollerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.journal_path = Path(self.tmp.name) / "journal.jsonl"
        self.journal = JsonlJournal(self.journal_path)
        self.service = TradingService(self.journal)

    def tearDown(self):
        self.tmp.cleanup()

    def test_poller_requires_token_to_start(self):
        from crypto_perp_tool.telegram_bot import TelegramCommandHandler
        handler = TelegramCommandHandler(self.service, allowed_chat_ids={123})
        poller = TelegramPoller(handler, token="")
        poller.start()
        self.assertFalse(poller.is_running())

    def test_poller_starts_and_stops(self):
        from crypto_perp_tool.telegram_bot import TelegramCommandHandler
        handler = TelegramCommandHandler(self.service, allowed_chat_ids={123})
        poller = TelegramPoller(handler, token="test_token", poll_interval=0.5)

        with mock.patch.object(poller, "_get_updates", return_value=[]):
            poller.start()
            self.assertTrue(poller.is_running())
            poller.stop()
            self.assertFalse(poller.is_running())

    def test_poller_stops_after_max_errors(self):
        from crypto_perp_tool.telegram_bot import TelegramCommandHandler
        handler = TelegramCommandHandler(self.service, allowed_chat_ids={123})
        poller = TelegramPoller(handler, token="test_token", poll_interval=0.1)
        poller._max_errors = 3

        with mock.patch.object(poller, "_get_updates", return_value=None):
            poller.start()
            poller._thread.join(timeout=3.0)
            self.assertFalse(poller.is_running())

    def test_poller_processes_updates(self):
        from crypto_perp_tool.telegram_bot import TelegramCommandHandler
        handler = TelegramCommandHandler(self.service, allowed_chat_ids={123})
        poller = TelegramPoller(handler, token="test_token", poll_interval=0.5, journal=self.journal)

        updates = [
            {
                "update_id": 1,
                "message": {
                    "chat": {"id": 123},
                    "text": "/status",
                },
            }
        ]
        send_calls = []
        with mock.patch.object(poller, "_get_updates", return_value=updates):
            with mock.patch.object(poller, "_send_message", side_effect=lambda cid, txt: send_calls.append((cid, txt))):
                poller.start()
                poller._thread.join(timeout=2.0)
                poller.stop()

        self.assertTrue(len(send_calls) > 0)
        chat_id, text = send_calls[0]
        self.assertEqual(chat_id, 123)
        self.assertIn("mode=paper", text)


class ParseAllowedChatIdsTests(unittest.TestCase):
    def test_empty_string_returns_empty_set(self):
        self.assertEqual(parse_allowed_chat_ids(""), set())
        self.assertEqual(parse_allowed_chat_ids("  "), set())

    def test_single_id(self):
        self.assertEqual(parse_allowed_chat_ids("123"), {123})

    def test_multiple_ids(self):
        self.assertEqual(parse_allowed_chat_ids("123,456,789"), {123, 456, 789})

    def test_ignores_invalid_ids(self):
        self.assertEqual(parse_allowed_chat_ids("123,abc,456"), {123, 456})

    def test_handles_spaces(self):
        self.assertEqual(parse_allowed_chat_ids(" 123 , 456 "), {123, 456})


if __name__ == "__main__":
    unittest.main()
