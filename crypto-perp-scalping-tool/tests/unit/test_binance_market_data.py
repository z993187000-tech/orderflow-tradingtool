import unittest

from crypto_perp_tool.market_data.binance import (
    BinanceAggTradeClient,
    BinanceAggTradeParser,
    BinanceBookTickerParser,
    BinanceExchangeInfoClient,
    BinanceExchangeInfoParser,
    BinanceMarkPriceParser,
    BinanceSpotTradeParser,
    BinanceStreamConfig,
    _INSTRUMENT_SPEC_CACHE,
    fetch_instrument_spec,
)


class BinanceMarketDataTests(unittest.TestCase):
    def test_stream_url_uses_usdm_futures_aggtrade_stream(self):
        config = BinanceStreamConfig(symbol="BTCUSDT")

        self.assertEqual(config.market_streams, ("btcusdt@aggTrade", "btcusdt@markPrice@1s"))
        self.assertEqual(config.public_streams, ("btcusdt@bookTicker",))
        self.assertEqual(config.spot_streams, ("btcusdt@trade",))
        self.assertEqual(config.market_url, "wss://fstream.binance.com/market/stream?streams=btcusdt@aggTrade/btcusdt@markPrice@1s")
        self.assertEqual(config.public_url, "wss://fstream.binance.com/public/stream?streams=btcusdt@bookTicker")
        self.assertEqual(config.spot_url, "wss://stream.binance.com:9443/stream?streams=btcusdt@trade")

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

    def test_mark_price_parser_converts_payload_to_mark_event(self):
        payload = {
            "e": "markPriceUpdate",
            "E": 1562305380000,
            "s": "BTCUSDT",
            "p": "11794.15000000",
            "i": "11784.62659091",
            "r": "0.00038167",
            "T": 1562306400000,
        }

        event = BinanceMarkPriceParser().parse(payload)

        self.assertEqual(event.timestamp, 1562305380000)
        self.assertEqual(event.symbol, "BTCUSDT")
        self.assertEqual(event.mark_price, 11794.15)
        self.assertEqual(event.index_price, 11784.62659091)
        self.assertEqual(event.funding_rate, 0.00038167)
        self.assertEqual(event.next_funding_time, 1562306400000)

    def test_spot_trade_parser_converts_payload_to_spot_event(self):
        payload = {
            "e": "trade",
            "E": 1672515782136,
            "s": "ETHUSDT",
            "p": "3200.25",
            "q": "0.20",
            "T": 1672515782135,
        }

        event = BinanceSpotTradeParser().parse(payload)

        self.assertEqual(event.timestamp, 1672515782135)
        self.assertEqual(event.symbol, "ETHUSDT")
        self.assertEqual(event.price, 3200.25)

    def test_client_routes_combined_stream_trade_quote_mark_and_spot_payloads(self):
        trades = []
        quotes = []
        marks = []
        spots = []
        client = BinanceAggTradeClient("BTCUSDT", on_trade=trades.append, on_quote=quotes.append, on_mark=marks.append, on_spot=spots.append)

        client._handle_payload({"stream": "btcusdt@bookTicker", "data": {"E": 1, "s": "BTCUSDT", "b": "100", "a": "102"}})
        client._handle_payload({"stream": "btcusdt@aggTrade", "data": {"E": 2, "T": 2, "s": "BTCUSDT", "p": "101", "q": "0.5", "m": False}})
        client._handle_payload({"stream": "btcusdt@markPrice@1s", "data": {"e": "markPriceUpdate", "E": 3, "s": "BTCUSDT", "p": "99", "i": "98", "r": "0.001", "T": 4}})
        client._handle_payload({"stream": "btcusdt@trade", "data": {"e": "trade", "E": 5, "T": 5, "s": "BTCUSDT", "p": "103", "q": "0.1"}})

        self.assertEqual(quotes[0].mid_price, 101)
        self.assertEqual(trades[0].price, 101)
        self.assertEqual(marks[0].mark_price, 99)
        self.assertEqual(spots[0].price, 103)

    def test_client_reports_status_changes(self):
        statuses = []

        client = BinanceAggTradeClient("BTCUSDT", on_trade=lambda event: None, on_status=lambda status, message: statuses.append((status, message)))
        client._report_status("connecting", "test")

        self.assertEqual(statuses, [("connecting", "test")])

    def test_exchange_info_parser_extracts_tick_and_step_size(self):
        payload = {
            "symbols": [
                {
                    "symbol": "BTCUSDT",
                    "filters": [
                        {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
                        {"filterType": "LOT_SIZE", "stepSize": "0.001"},
                    ],
                }
            ]
        }

        spec = BinanceExchangeInfoParser().parse_symbol(payload, "BTCUSDT")

        self.assertEqual(spec.symbol, "BTCUSDT")
        self.assertEqual(spec.tick_size, 0.1)
        self.assertEqual(spec.step_size, 0.001)
        self.assertEqual(spec.taker_fee_rate, 0.0004)

    def test_exchange_info_client_uses_usdm_futures_endpoint(self):
        requested = []

        def fake_loader(url: str, timeout: float):
            requested.append((url, timeout))
            return {
                "symbols": [
                    {
                        "symbol": "ETHUSDT",
                        "filters": [
                            {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
                            {"filterType": "LOT_SIZE", "stepSize": "0.001"},
                        ],
                    }
                ]
            }

        client = BinanceExchangeInfoClient(loader=fake_loader, timeout_seconds=7)
        spec = client.fetch_symbol("ETHUSDT")

        self.assertEqual(requested, [("https://fapi.binance.com/fapi/v1/exchangeInfo", 7)])
        self.assertEqual(spec.tick_size, 0.01)
        self.assertEqual(spec.step_size, 0.001)

    def test_fetch_instrument_spec_uses_cache(self):
        _INSTRUMENT_SPEC_CACHE.clear()
        _INSTRUMENT_SPEC_CACHE["BTCUSDT"] = BinanceExchangeInfoParser().parse_symbol(
            {
                "symbols": [
                    {
                        "symbol": "BTCUSDT",
                        "filters": [
                            {"filterType": "PRICE_FILTER", "tickSize": "0.50"},
                            {"filterType": "LOT_SIZE", "stepSize": "0.010"},
                        ],
                    }
                ]
            },
            "BTCUSDT",
        )

        spec = fetch_instrument_spec("BTCUSDT")

        self.assertEqual(spec.tick_size, 0.5)
        self.assertEqual(spec.step_size, 0.01)
        _INSTRUMENT_SPEC_CACHE.clear()


if __name__ == "__main__":
    unittest.main()
