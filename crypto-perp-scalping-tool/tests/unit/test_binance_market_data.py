import unittest
import unittest.mock

from crypto_perp_tool.market_data.binance import (
    BinanceAggTradeClient,
    BinanceAggTradeParser,
    BinanceBookTickerParser,
    BinanceExchangeInfoClient,
    BinanceExchangeInfoParser,
    BinanceHistoricalKlineClient,
    BinanceMarkPriceParser,
    BinanceSpotTradeParser,
    BinanceStreamConfig,
    _INSTRUMENT_SPEC_CACHE,
    fetch_instrument_spec,
)


class BinanceMarketDataTests(unittest.TestCase):
    def test_stream_url_uses_usdm_futures_aggtrade_stream(self):
        config = BinanceStreamConfig(symbol="BTCUSDT")

        self.assertEqual(config.market_streams, ("btcusdt@aggTrade", "btcusdt@markPrice@1s", "btcusdt@forceOrder", "btcusdt@kline_5m"))
        self.assertEqual(config.public_streams, ("btcusdt@bookTicker",))
        self.assertEqual(config.spot_streams, ("btcusdt@trade",))
        self.assertEqual(config.market_url, "wss://fstream.binance.com/market/stream?streams=btcusdt@aggTrade/btcusdt@markPrice@1s/btcusdt@forceOrder/btcusdt@kline_5m")
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
        self.assertEqual(getattr(event, "exchange_event_time", None), 1569514978020)
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


class BinanceAuthenticatedClientTests(unittest.TestCase):
    def test_factory_returns_none_in_paper_mode(self):
        from crypto_perp_tool.config import default_settings
        from crypto_perp_tool.market_data.binance import create_authenticated_client_if_live
        client = create_authenticated_client_if_live(default_settings())
        self.assertIsNone(client)

    def test_factory_returns_none_without_env_vars(self):
        from crypto_perp_tool.config import load_settings
        from crypto_perp_tool.market_data.binance import create_authenticated_client_if_live
        with unittest.mock.patch.dict("os.environ", {}, clear=True):
            settings = load_settings({"mode": "live"})
            client = create_authenticated_client_if_live(settings)
        self.assertIsNone(client)

    def test_fetch_positions_parses_response(self):
        import io
        import json
        from unittest.mock import patch
        from crypto_perp_tool.market_data.binance import BinanceAuthenticatedClient

        fake_response = json.dumps([
            {"symbol": "BTCUSDT", "positionAmt": "0.010", "entryPrice": "96000.0",
             "unRealizedProfit": "15.50", "leverage": "3"},
            {"symbol": "ETHUSDT", "positionAmt": "-0.500", "entryPrice": "3200.0",
             "unRealizedProfit": "-5.25", "leverage": "2"},
            {"symbol": "BNBUSDT", "positionAmt": "0.000", "entryPrice": "0",
             "unRealizedProfit": "0", "leverage": "1"},
        ]).encode()

        with patch("crypto_perp_tool.market_data.binance.urlopen", return_value=io.BytesIO(fake_response)):
            client = BinanceAuthenticatedClient(api_key="k", api_secret="s")
            positions = client.fetch_positions()
            self.assertIn("BTCUSDT", positions)
            self.assertEqual(positions["BTCUSDT"]["side"], "long")
            self.assertEqual(positions["BTCUSDT"]["quantity"], 0.01)
            self.assertIn("ETHUSDT", positions)
            self.assertEqual(positions["ETHUSDT"]["side"], "short")
            self.assertNotIn("BNBUSDT", positions)

    def test_fetch_open_orders_parses_response(self):
        import io
        import json
        from unittest.mock import patch
        from crypto_perp_tool.market_data.binance import BinanceAuthenticatedClient

        fake_response = json.dumps([
            {"symbol": "BTCUSDT", "orderId": 123, "type": "STOP_MARKET", "side": "SELL",
             "price": "0", "stopPrice": "95500", "origQty": "0.01",
             "reduceOnly": True, "status": "NEW"},
        ]).encode()

        with patch("crypto_perp_tool.market_data.binance.urlopen", return_value=io.BytesIO(fake_response)):
            client = BinanceAuthenticatedClient(api_key="k", api_secret="s")
            orders = client.fetch_open_orders(symbol="BTCUSDT")
            self.assertIn("BTCUSDT", orders)
            self.assertEqual(orders["BTCUSDT"][0]["type"], "STOP_MARKET")
            self.assertTrue(orders["BTCUSDT"][0]["reduceOnly"])

    def test_http_418_raises_immediately(self):
        import urllib.error
        from unittest.mock import patch
        from crypto_perp_tool.market_data.binance import BinanceAuthenticatedClient

        def raise_418(req, timeout=None):
            raise urllib.error.HTTPError(url="https://fapi.binance.com/test", code=418, msg="IP banned", hdrs={}, fp=None)

        with patch("crypto_perp_tool.market_data.binance.urlopen", side_effect=raise_418):
            client = BinanceAuthenticatedClient(api_key="k", api_secret="s")
            with self.assertRaises(RuntimeError) as ctx:
                client.fetch_positions(symbol="BTCUSDT")
            self.assertIn("418", str(ctx.exception))

    def test_http_429_retries_then_raises(self):
        import urllib.error
        from unittest.mock import patch
        from crypto_perp_tool.market_data.binance import BinanceAuthenticatedClient

        def raise_429(req, timeout=None):
            raise urllib.error.HTTPError(url="https://fapi.binance.com/test", code=429, msg="Rate limited", hdrs={}, fp=None)

        with patch("crypto_perp_tool.market_data.binance.urlopen", side_effect=raise_429):
            client = BinanceAuthenticatedClient(api_key="k", api_secret="s")
            client.RETRY_DELAY_SECONDS = 0.001
            with self.assertRaises(RuntimeError) as ctx:
                client.fetch_positions(symbol="BTCUSDT")
            self.assertIn("rate limit", str(ctx.exception).lower())


class BinanceHistoricalAggTradeClientTests(unittest.TestCase):
    def test_fetch_returns_parsed_trade_events(self):
        import io
        import json
        from unittest.mock import patch
        from crypto_perp_tool.market_data.binance import BinanceHistoricalAggTradeClient

        fake_response = json.dumps([
            {"a": 1001, "p": "96000.00", "q": "0.500", "T": 1700000000000, "m": False},
            {"a": 1002, "p": "96010.00", "q": "1.200", "T": 1700000001000, "m": True},
        ]).encode()

        with patch("crypto_perp_tool.market_data.binance.urlopen", return_value=io.BytesIO(fake_response)):
            client = BinanceHistoricalAggTradeClient()
            trades = client.download("BTCUSDT", max_pages=1)

        self.assertEqual(len(trades), 2)
        self.assertEqual(trades[0].symbol, "BTCUSDT")
        self.assertEqual(trades[0].price, 96000.00)
        self.assertFalse(trades[0].is_buyer_maker)
        self.assertEqual(trades[1].quantity, 1.2)
        self.assertTrue(trades[1].is_buyer_maker)

    def test_fetch_builds_correct_url(self):
        from unittest.mock import MagicMock
        from crypto_perp_tool.market_data.binance import BinanceHistoricalAggTradeClient

        client = BinanceHistoricalAggTradeClient()
        fake_loader = MagicMock(return_value=[])
        client.loader = fake_loader

        client.fetch("btcusdt", start_time=1700000000000, end_time=1700000100000, limit=500)
        url = fake_loader.call_args[0][0]
        self.assertIn("symbol=BTCUSDT", url)
        self.assertIn("startTime=1700000000000", url)
        self.assertIn("endTime=1700000100000", url)
        self.assertIn("limit=500", url)
        self.assertIn("/fapi/v1/aggTrades", url)


class BinanceHistoricalKlineClientTests(unittest.TestCase):
    def test_download_returns_parsed_kline_events(self):
        payload = [
            [
                1700000000000,
                "96000.00",
                "96150.00",
                "95900.00",
                "96100.00",
                "12.500",
                1700000299999,
                "1200000.00",
                321,
                "6.200",
                "595000.00",
                "0",
            ],
            [
                1700000300000,
                "96100.00",
                "96200.00",
                "96050.00",
                "96180.00",
                "9.250",
                1700000599999,
                "890000.00",
                244,
                "4.100",
                "395000.00",
                "0",
            ],
        ]
        client = BinanceHistoricalKlineClient(loader=lambda url, timeout: payload)

        klines = client.download("BTCUSDT", interval="5m", limit=2)

        self.assertEqual(len(klines), 2)
        self.assertEqual(klines[0].symbol, "BTCUSDT")
        self.assertEqual(klines[0].interval, "5m")
        self.assertEqual(klines[0].timestamp, 1700000000000)
        self.assertEqual(klines[0].close_time, 1700000299999)
        self.assertEqual(klines[0].open, 96000.00)
        self.assertEqual(klines[0].high, 96150.00)
        self.assertEqual(klines[0].low, 95900.00)
        self.assertEqual(klines[0].close, 96100.00)
        self.assertEqual(klines[0].volume, 12.5)
        self.assertEqual(klines[0].quote_volume, 1200000.0)
        self.assertEqual(klines[0].trade_count, 321)
        self.assertTrue(klines[0].is_closed)

    def test_fetch_builds_futures_klines_url(self):
        requested = []

        def fake_loader(url: str, timeout: float):
            requested.append(url)
            return []

        client = BinanceHistoricalKlineClient(loader=fake_loader)

        client.fetch("btcusdt", interval="5m", limit=96, start_time=1700000000000, end_time=1700028800000)
        url = requested[0]

        self.assertIn("symbol=BTCUSDT", url)
        self.assertIn("interval=5m", url)
        self.assertIn("limit=96", url)
        self.assertIn("startTime=1700000000000", url)
        self.assertIn("endTime=1700028800000", url)
        self.assertIn("/fapi/v1/klines", url)


if __name__ == "__main__":
    unittest.main()
