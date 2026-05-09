import unittest

from crypto_perp_tool.web.health import health_payload


class HealthTests(unittest.TestCase):
    def test_health_payload_is_ready_for_zeabur_probe(self):
        payload = health_payload(source="binance", symbol="BTCUSDT")

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["service"], "crypto-perp-scalping-tool")
        self.assertEqual(payload["source"], "binance")
        self.assertEqual(payload["symbol"], "BTCUSDT")


if __name__ == "__main__":
    unittest.main()
