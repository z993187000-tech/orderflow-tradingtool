import unittest

from crypto_perp_tool.market_data.binance import (
    BinanceAggTradeClient,
    BinanceAggTradeParser,
    BinanceBookTickerParser,
    BinanceStreamConfig,
)


class BinanceMarketDataTests(unittest.TestCase):
    def test_stream_url_uses_usdm_futures_aggtrade_stream(self):
        config = BinanceStreamConfig(symbol="BTCUSDT")

        self.assertEqual(config.streams, ("btcusdt@aggTrade", "btcusdt@bookTicker"))
        self.assertEqual(config.url, "wss://fstream.binance.com/stream?streams=btcusdt@aggTrade/btcusdt@bookTicker")

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

    def test_book_ticker_parser_converts_payload_to_quote_event(self):
        payload = {
            "e": "bookTicker",
            "E": 1569514978123,
            "s": "BTCUSDT",
            "b": "80100.10",
            "a": "80100.30",
        }

        event = BinanceBookTickerParser().parse(payload)

        self.assertEqual(event.timestamp, 1569514978123)
        self.assertEqual(event.symbol, "BTCUSDT")
        self.assertEqual(event.bid_price, 80100.10)
        self.assertEqual(event.ask_price, 80100.30)
        self.assertAlmostEqual(event.mid_price, 80100.20)

    def test_client_routes_combined_stream_trade_and_quote_payloads(self):
        trades = []
        quotes = []
        client = BinanceAggTradeClient("BTCUSDT", on_trade=trades.append, on_quote=quotes.append)

        client._handle_payload({"stream": "btcusdt@bookTicker", "data": {"E": 1, "s": "BTCUSDT", "b": "100", "a": "102"}})
        client._handle_payload({"stream": "btcusdt@aggTrade", "data": {"E": 2, "T": 2, "s": "BTCUSDT", "p": "101", "q": "0.5", "m": False}})

        self.assertEqual(quotes[0].mid_price, 101)
        self.assertEqual(trades[0].price, 101)

    def test_client_reports_status_changes(self):
        statuses = []

        client = BinanceAggTradeClient("BTCUSDT", on_trade=lambda event: None, on_status=lambda status, message: statuses.append((status, message)))
        client._report_status("connecting", "test")

        self.assertEqual(statuses, [("connecting", "test")])


if __name__ == "__main__":
    unittest.main()
