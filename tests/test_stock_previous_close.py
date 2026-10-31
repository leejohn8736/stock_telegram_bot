import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import stock_api


KST = timezone(timedelta(hours=9))
EDT = timezone(timedelta(hours=-4))


def chart(tz, last_day, regular_start):
    dates = [datetime(2026, 8, day, 12, tzinfo=tz) for day in range(1, 20)]
    dates += [datetime(2026, 9, 23, 12, tzinfo=tz)]
    if last_day == 24:
        dates += [datetime(2026, 9, 24, 12, tzinfo=tz)]
        closes = [100.0] * (len(dates) - 2) + [105.0, 110.0]
    else:
        closes = [100.0] * (len(dates) - 1) + [105.0]
    return {"chart": {"result": [{
        "meta": {
            "currency": "KRW" if tz == KST else "USD",
            "instrumentType": "EQUITY",
            "currentTradingPeriod": {"regular": {"start": regular_start.timestamp()}},
            "previousClose": 99.0,
        },
        "timestamp": [int(day.timestamp()) for day in dates],
        "indicators": {"quote": [{
            "open": closes, "high": closes, "low": closes,
            "close": closes, "volume": [100] * len(closes),
        }]},
    }]}}


class PreviousCloseTests(unittest.TestCase):
    def check_daily(self, ticker, tz, now, last_day, regular_start, expected):
        with patch("stock_api._make_request", return_value=chart(tz, last_day, regular_start)), \
             patch("stock_api._get_nasdaq_previous_close", return_value=None), \
             patch("toss_api.time.time", return_value=now.timestamp()):
            daily = stock_api._get_daily_data(ticker)
        self.assertEqual(daily["previous_close"], expected)

    def test_us_after_close_when_trading_period_points_to_tomorrow(self):
        self.check_daily(
            "AAPL", EDT, datetime(2026, 9, 24, 17, tzinfo=EDT), 24,
            datetime(2026, 9, 25, 9, 30, tzinfo=EDT), 105.0,
        )

    def test_korea_after_close_when_trading_period_points_to_tomorrow(self):
        self.check_daily(
            "005930.KS", KST, datetime(2026, 9, 24, 16, tzinfo=KST), 24,
            datetime(2026, 9, 25, 9, tzinfo=KST), 105.0,
        )

    def test_during_market_with_today_candle(self):
        self.check_daily(
            "AAPL", EDT, datetime(2026, 9, 24, 12, 30, tzinfo=EDT), 24,
            datetime(2026, 9, 24, 9, 30, tzinfo=EDT), 105.0,
        )

    def test_before_market_without_today_candle(self):
        self.check_daily(
            "AAPL", EDT, datetime(2026, 9, 24, 8, tzinfo=EDT), 23,
            datetime(2026, 9, 24, 9, 30, tzinfo=EDT), 105.0,
        )

    def test_missing_yahoo_close_uses_nasdaq(self):
        now = datetime(2026, 9, 23, 12, tzinfo=EDT)
        data = chart(EDT, 24, datetime(2026, 9, 23, 9, 30, tzinfo=EDT))
        result = data["chart"]["result"][0]
        result["meta"]["instrumentType"] = "ETF"
        result["timestamp"][-2:] = [
            int(datetime(2026, 9, 22, 9, 30, tzinfo=EDT).timestamp()),
            int(datetime(2026, 9, 23, 9, 30, tzinfo=EDT).timestamp()),
        ]
        for field in ("open", "high", "low", "close"):
            result["indicators"]["quote"][0][field][-2] = None
        with patch("stock_api._make_request", return_value=data), \
             patch("stock_api._get_nasdaq_previous_close", return_value=151.95) as nasdaq, \
             patch("toss_api.time.time", return_value=now.timestamp()):
            daily = stock_api._get_daily_data("SOXL")
        self.assertEqual(daily["previous_close"], 151.95)
        nasdaq.assert_called_once_with("SOXL", "ETF", "2026-09-22")

    def test_missing_yahoo_close_is_not_replaced_with_older_day(self):
        now = datetime(2026, 9, 23, 12, tzinfo=EDT)
        data = chart(EDT, 24, datetime(2026, 9, 23, 9, 30, tzinfo=EDT))
        result = data["chart"]["result"][0]
        result["timestamp"][-2:] = [
            int(datetime(2026, 9, 22, 9, 30, tzinfo=EDT).timestamp()),
            int(datetime(2026, 9, 23, 9, 30, tzinfo=EDT).timestamp()),
        ]
        result["indicators"]["quote"][0]["close"][-2] = None
        with patch("stock_api._make_request", return_value=data), \
             patch("stock_api._get_nasdaq_previous_close", return_value=None), \
             patch("toss_api.time.time", return_value=now.timestamp()):
            daily = stock_api._get_daily_data("ASTS")
        self.assertIsNone(daily["previous_close"])

    def test_nasdaq_close_parses_stock_and_etf_prices(self):
        rows = [{"date": "09/22/2026", "close": "$63.69"}]
        payload = {"status": {"rCode": 200}, "data": {"tradesTable": {"rows": rows}}}
        with patch.dict(stock_api._nasdaq_close_cache, {}, clear=True), \
             patch("stock_api._make_request", return_value=payload) as request:
            self.assertEqual(stock_api._get_nasdaq_previous_close("ASTS", "EQUITY", "2026-09-22"), 63.69)
            self.assertIn("assetclass=stocks", request.call_args.args[0])
            rows[0]["close"] = "151.95"
            self.assertEqual(stock_api._get_nasdaq_previous_close("SOXL", "ETF", "2026-09-22"), 151.95)
            self.assertIn("assetclass=etf", request.call_args.args[0])

    def test_nasdaq_tries_etf_when_toss_has_no_instrument_type(self):
        missing = {"status": {"rCode": 400}}
        found = {"status": {"rCode": 200}, "data": {"tradesTable": {
            "rows": [{"date": "09/22/2026", "close": "151.95"}],
        }}}
        with patch.dict(stock_api._nasdaq_close_cache, {}, clear=True), \
             patch("stock_api._make_request", side_effect=[missing, found]) as request:
            self.assertEqual(stock_api._get_nasdaq_previous_close("SOXL", None, "2026-09-22"), 151.95)
        self.assertIn("assetclass=stocks", request.call_args_list[0].args[0])
        self.assertIn("assetclass=etf", request.call_args_list[1].args[0])

    def test_toss_prices_use_verified_us_previous_close(self):
        daily = {
            "timestamps": [1, 2], "closes": [141.93, 143.0],
            "highs": [142.0, 144.0], "lows": [140.0, 142.0],
            "opens": [141.0, 142.0], "volumes": [100, 200],
            "currency": "USD", "previous_close": 141.93,
        }
        price_cache = {"SOXL": {"price": 143.5, "currency": "USD"}}
        with patch("stock_api.toss_api.build_daily_data", return_value=daily), \
             patch("stock_api._get_us_previous_close", return_value=151.95), \
             patch("stock_api.fetch_stock_name", return_value="SOXL"):
            full = stock_api._fetch_stock_data_toss("SOXL", price_cache)
            light = stock_api._fetch_current_price_only_toss("SOXL", price_cache)
        self.assertEqual(full["current_price"], 143.5)
        self.assertEqual(full["previous_close"], 151.95)
        self.assertEqual(light["previous_close"], 151.95)

    def test_price_alert_uses_nasdaq_when_yahoo_day_is_missing(self):
        now = datetime(2026, 9, 23, 12, tzinfo=EDT)
        yesterday = datetime(2026, 9, 22, 9, 30, tzinfo=EDT)
        minute = datetime(2026, 9, 23, 11, 59, tzinfo=EDT)
        daily = {"chart": {"result": [{
            "meta": {"instrumentType": "EQUITY"},
            "timestamp": [int(yesterday.timestamp())],
            "indicators": {"quote": [{"close": [None]}]},
        }]}}
        intraday = {"chart": {"result": [{
            "meta": {"currency": "USD"},
            "timestamp": [int(minute.timestamp())],
            "indicators": {"quote": [{"close": [61.58], "high": [61.6], "low": [61.5]}]},
        }]}}
        with patch("stock_api._make_request", side_effect=[daily, intraday]), \
             patch("stock_api._get_nasdaq_previous_close", return_value=63.69), \
             patch("toss_api.time.time", return_value=now.timestamp()):
            snapshot = stock_api.fetch_price_alert_snapshot("ASTS")
        self.assertEqual(snapshot["previous_close"], 63.69)
        self.assertEqual(snapshot["current_price"], 61.58)


if __name__ == "__main__":
    unittest.main()
