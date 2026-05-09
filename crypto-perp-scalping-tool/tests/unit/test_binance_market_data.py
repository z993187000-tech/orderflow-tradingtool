import unittest

from crypto_perp_tool.market_data.binance import BinanceAggTradeClient, BinanceAggTradeParser, BinanceStreamConfig


class BinanceMarketDataTests(unittest.TestCase):
    def test_stream_url_uses_usdm_futures_aggtrade_stream(self):
        config = BinanceStreamConfig(symbol="BTCUSDT")

        self.assertEqual(config.stream_name, "btcusdt@aggTrade")
        self.assertEqual(config.url, "wss://fstream.binance.com/market/ws/btcusdt@aggTrade")

    def test_parser_converts_aggtrade_payload_to_trade_event(self):
        payload = {
            "e": "aggTrade",
            "E": 1569514978020,
            "s": "BTCUSDT",
            "a": 12345,
            "p": "27123.40",
            "q": "0.018",
            "T": 1569514978020,
            "m": True,
        }

        event = BinanceAggTradeParser().parse(payload)

        self.assertEqual(event.timestamp, 1569514978020)
        self.assertEqual(event.symbol, "BTCUSDT")
        self.assertEqual(event.price, 27123.40)
        self.assertEqual(event.quantity, 0.018)
        self.assertTrue(event.is_buyer_maker)
        self.assertEqual(event.delta, -0.018)

    def test_client_reports_status_changes(self):
        statuses = []

        client = BinanceAggTradeClient("BTCUSDT", on_trade=lambda event: None, on_status=lambda status, message: statuses.append((status, message)))
        client._report_status("connecting", "test")

        self.assertEqual(statuses, [("connecting", "test")])


if __name__ == "__main__":
    unittest.main()
