import json
import tempfile
import unittest
from pathlib import Path

from crypto_perp_tool.journal import JsonlJournal


class JournalTests(unittest.TestCase):
    def test_journal_redacts_sensitive_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            journal = JsonlJournal(path)

            journal.write(
                "telegram_command",
                {
                    "telegram_token": "123456789:secret-token",
                    "api_key": "abcdef1234567890",
                    "message": "status",
                },
            )

            event = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(event["type"], "telegram_command")
        self.assertEqual(event["payload"]["telegram_token"], "***REDACTED***")
        self.assertEqual(event["payload"]["api_key"], "***REDACTED***")
        self.assertEqual(event["payload"]["message"], "status")


if __name__ == "__main__":
    unittest.main()
