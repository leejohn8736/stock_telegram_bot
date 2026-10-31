import copy
import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

import market_indices
from scheduler import AlertScheduler


TRADING_DATE = "2026-09-22"
KST = timezone(timedelta(hours=9))


def naver_item(code="KOSPI", **changes):
    # Public polling response fields; use synthetic prices for deterministic checks.
    item = {
        "itemCode": code,
        "marketStatus": "CLOSE",
        "localTradedAt": f"{TRADING_DATE}T15:30:00+09:00",
        "stockExchangeType": {"endTime": "1530", "delayTime": 0},
        "closePrice": "2,550.00",
        "compareToPreviousClosePrice": "50.00",
        "compareToPreviousPrice": {"code": "2"},
    }
    item.update(changes)
    return item


def yahoo_chart():
    close_at = datetime(2026, 9, 22, 15, 30, tzinfo=KST).timestamp()
    return {
        "meta": {
            "regularMarketTime": close_at,
            "regularMarketPrice": 2550.0,
            "currentTradingPeriod": {"regular": {"end": close_at}},
        },
        "timestamp": [close_at - 86400, close_at - 6.5 * 3600],
        "indicators": {"quote": [{"close": [2500.0, 2550.0]}]},
    }


def naver_daily_closes(value="2,550.00", previous_close="2,500.00"):
    return [
        {"localTradedAt": TRADING_DATE, "closePrice": value},
        {"localTradedAt": "2026-09-21", "closePrice": previous_close},
    ]


class NaverCloseTests(unittest.TestCase):
    def fetch(self, item, daily=None):
        if daily is None:
            daily = naver_daily_closes()
        with patch("market_indices._make_request", side_effect=[
            json.dumps({"datas": [item]}), json.dumps(daily),
        ]):
            return market_indices._fetch_naver_korea_index_close("KOSPI", TRADING_DATE)

    def test_confirmed_close_at_1530_without_yahoo_delay(self):
        quote = self.fetch(naver_item())
        self.assertEqual(quote["value"], 2550.0)
        self.assertEqual(quote["previous_close"], 2500.0)
        self.assertEqual(quote["change"], 50.0)
        self.assertEqual(quote["change_pct"], 2.0)
        self.assertEqual(quote["date"], TRADING_DATE)

    def test_falling_and_unchanged_prices(self):
        for amount in ("-50.00", "50.00"):
            with self.subTest(amount=amount):
                quote = self.fetch(naver_item(
                    closePrice="2,450.00", compareToPreviousClosePrice=amount,
                    compareToPreviousPrice={"code": "5"}),
                    naver_daily_closes("2,450.00"))
                self.assertEqual(quote["previous_close"], 2500.0)
                self.assertEqual(quote["change_pct"], -2.0)
        quote = self.fetch(naver_item(
            compareToPreviousClosePrice="0", compareToPreviousPrice={"code": "3"}),
            naver_daily_closes(previous_close="2,550.00"))
        self.assertEqual(quote["change_pct"], 0)
        self.assertEqual(quote["previous_close"], 2550.0)

    def test_rejects_open_stale_early_or_unrelated_quotes(self):
        for changes in (
            {"marketStatus": "OPEN"},
            {"marketStatus": "UNKNOWN"},
            {"localTradedAt": "2026-09-21T15:30:00+09:00"},
            {"localTradedAt": "2026-09-22T15:29:59+09:00"},
            {"localTradedAt": "2026-09-22T15:30:00"},
            {"itemCode": "KOSDAQ"},
            {"stockExchangeType": {"endTime": "1630"}},
        ):
            with self.subTest(changes=changes):
                self.assertIsNone(self.fetch(naver_item(**changes)))

    def test_extended_session_and_timezone(self):
        self.assertIsNotNone(self.fetch(naver_item(
            localTradedAt="2026-09-22T16:30:00+09:00",
            stockExchangeType={"endTime": "1630"})))
        self.assertIsNotNone(self.fetch(naver_item(localTradedAt="2026-09-22T06:30:00+00:00")))

    def test_rejects_invalid_prices_and_missing_fields(self):
        for changes in (
            {"closePrice": "NaN"}, {"closePrice": "inf"}, {"closePrice": "0"},
            {"closePrice": None}, {"closePrice": "--"},
            {"compareToPreviousClosePrice": "Infinity"},
            {"compareToPreviousClosePrice": "3000"},
            {"compareToPreviousPrice": {"code": "UNKNOWN"}},
            {"compareToPreviousPrice": {"code": "3"}},
            {"stockExchangeType": {}}, {"stockExchangeType": None},
        ):
            with self.subTest(changes=changes):
                self.assertIsNone(self.fetch(naver_item(**changes)))
        for field in naver_item():
            item = naver_item()
            del item[field]
            with self.subTest(missing=field):
                self.assertIsNone(self.fetch(item))

    def test_bad_responses_return_none(self):
        for response in (None, "not json", "null", "[]", '{"datas": null}', '{"datas": []}'):
            with self.subTest(response=response), patch("market_indices._make_request", return_value=response):
                self.assertIsNone(market_indices._fetch_naver_korea_index_close("KOSPI", TRADING_DATE))

    def test_rejects_close_until_daily_price_and_previous_day_agree(self):
        cases = [
            naver_daily_closes(value="2,549.00"),
            naver_daily_closes(previous_close="2,499.00"),
            naver_daily_closes(value="NaN"),
            naver_daily_closes(previous_close="0"),
            naver_daily_closes()[:1],
            naver_daily_closes()[1:],
            [{"localTradedAt": "2026-09-18", "closePrice": "2,500.00"},
             naver_daily_closes()[0]],
            [], {}, {"error": "unavailable"},
        ]
        for daily in cases:
            with self.subTest(daily=daily):
                self.assertIsNone(self.fetch(naver_item(), daily))

    def test_daily_prices_are_selected_by_date_in_any_order(self):
        daily = list(reversed(naver_daily_closes()))
        daily.insert(0, {"localTradedAt": "2026-09-23", "closePrice": "9,999.00"})
        self.assertEqual(self.fetch(naver_item(), daily)["value"], 2550.0)

    def test_unavailable_or_malformed_daily_prices_return_none(self):
        for response in (None, "not json", "null", '[null]', '[{}]'):
            with self.subTest(response=response), patch("market_indices._make_request", side_effect=[
                json.dumps({"datas": [naver_item()]}), response,
            ]):
                self.assertIsNone(market_indices._fetch_naver_korea_index_close("KOSPI", TRADING_DATE))


class CloseSourceTests(unittest.TestCase):
    def test_naver_success_does_not_wait_for_yahoo(self):
        with patch("market_indices._make_request", side_effect=[
            json.dumps({"datas": [naver_item()]}), json.dumps(naver_daily_closes()),
        ]) as request:
            quote = market_indices._fetch_korea_index_close("^KS11", "KOSPI", TRADING_DATE)
        self.assertEqual(quote["value"], 2550.0)
        self.assertIn("Naver", quote["source"])
        self.assertEqual(request.call_count, 2)
        self.assertTrue(all("naver.com" in call.args[0] for call in request.call_args_list))

    def test_unavailable_naver_falls_back_to_confirmed_yahoo_close(self):
        response = json.dumps({"chart": {"result": [yahoo_chart()]}})
        with patch("market_indices._make_request", side_effect=[None, response]):
            quote = market_indices._fetch_korea_index_close("^KS11", "KOSPI", TRADING_DATE)
        self.assertEqual(quote["value"], 2550.0)
        self.assertEqual(quote["change_pct"], 2.0)
        self.assertIn("Yahoo", quote["source"])

    def test_yahoo_fallback_requires_the_exact_previous_trading_day(self):
        chart = yahoo_chart()
        chart["timestamp"][0] -= 3 * 86400  # Monday is absent, only the older Friday remains.
        with patch("market_indices._make_request", return_value=json.dumps({"chart": {"result": [chart]}})):
            self.assertIsNone(market_indices._fetch_yahoo_korea_index_close("^KS11", "KOSPI", TRADING_DATE))

    def test_yahoo_fallback_skips_weekends_and_holidays(self):
        for trading_date, previous_date in (
            ("2026-09-21", "2026-09-18"),
            ("2026-09-28", "2026-09-23"),
            ("2026-05-04", "2026-04-30"),  # Labor Day
            ("2026-06-04", "2026-06-02"),  # Registered election closure
            ("2026-06-09", "2026-06-08"),  # No substitute for Memorial Day
            ("2027-01-04", "2026-12-30"),  # Year-end closure and New Year's Day
        ):
            chart = yahoo_chart()
            close_at = datetime.fromisoformat(trading_date).replace(hour=15, minute=30, tzinfo=KST).timestamp()
            chart["meta"]["regularMarketTime"] = close_at
            chart["meta"]["currentTradingPeriod"]["regular"]["end"] = close_at
            chart["timestamp"] = [
                datetime.fromisoformat(previous_date).replace(hour=9, tzinfo=KST).timestamp(),
                close_at - 6.5 * 3600,
            ]
            with self.subTest(trading_date=trading_date), patch("market_indices._make_request", return_value=json.dumps({"chart": {"result": [chart]}})):
                quote = market_indices._fetch_yahoo_korea_index_close("^KS11", "KOSPI", trading_date)
                self.assertEqual(quote["previous_close"], 2500.0)

    def test_out_of_sync_naver_and_yahoo_prices_wait_then_recover(self):
        pending = yahoo_chart()
        pending["meta"]["regularMarketTime"] -= 1
        responses = [
            json.dumps({"datas": [naver_item()]}),
            json.dumps(naver_daily_closes(value="2,549.00")),
            json.dumps({"chart": {"result": [pending]}}),
            json.dumps({"datas": [naver_item()]}),
            json.dumps(naver_daily_closes()),
        ]
        with patch("market_indices._make_request", side_effect=responses):
            self.assertIsNone(market_indices._fetch_korea_index_close("^KS11", "KOSPI", TRADING_DATE))
            self.assertEqual(market_indices._fetch_korea_index_close("^KS11", "KOSPI", TRADING_DATE)["value"], 2550.0)

    def test_yahoo_fallback_rejects_stale_or_unconfirmed_candles(self):
        base = yahoo_chart()
        cases = []
        for seconds in (1, 86400):
            chart = copy.deepcopy(base)
            chart["meta"]["regularMarketTime"] -= seconds
            cases.append(chart)
        chart = copy.deepcopy(base)
        chart["meta"]["currentTradingPeriod"]["regular"]["end"] += 3600
        cases.append(chart)
        for values in ([2500.0, 2549.0], [2500.0, None], [None, 2550.0]):
            chart = copy.deepcopy(base)
            chart["indicators"]["quote"][0]["close"] = values
            cases.append(chart)
        for chart in cases:
            with self.subTest(chart=chart), patch("market_indices._make_request", side_effect=[
                None, json.dumps({"chart": {"result": [chart]}}),
            ]):
                self.assertIsNone(market_indices._fetch_korea_index_close("^KS11", "KOSPI", TRADING_DATE))

    def test_partial_close_is_not_reported_or_cached_and_next_fetch_recovers(self):
        quotes = {code.lower(): naver_item(code) for code in ("KOSPI", "KOSDAQ")}
        responses = [dict(quotes, kosdaq=None), quotes]
        with patch.dict(market_indices._result_cache, {}, clear=True), \
                patch("market_indices.fetch_korea_market_closing_indices", side_effect=responses) as fetch, \
                patch("market_indices._run_concurrently", return_value={}) as extras:
            self.assertIsNone(market_indices.fetch_korea_market_close_data(TRADING_DATE))
            extras.assert_not_called()
            data = market_indices.fetch_korea_market_close_data(TRADING_DATE)
            self.assertEqual(data["korea_indices"], quotes)
            self.assertIs(market_indices.fetch_korea_market_close_data(TRADING_DATE), data)
            self.assertEqual(fetch.call_count, 2)


class CloseScheduleTests(unittest.TestCase):
    def setUp(self):
        self.bot = Mock()
        self.bot.send_message.return_value = 123
        self.scheduler = AlertScheduler(self.bot)
        patches = {
            "clock": patch("market_calendar.get_korea_now", return_value=datetime(2026, 9, 22, 15, 30)),
            "subscriptions": patch("database.get_all_subscriptions", return_value=[(1, "AAPL"), (1, "MSFT")]),
            "level": patch("database.should_send_alert", return_value=True),
            "topic": patch("database.get_chat_topic", return_value=None),
            "fetch": patch("market_indices.fetch_korea_market_close_data", return_value={
                "date": TRADING_DATE, "korea_indices": {
                    "kospi": {"name": "KOSPI", "value": 2550, "change": 50, "change_pct": 2},
                    "kosdaq": {"name": "KOSDAQ", "value": 750, "change": -10, "change_pct": -1.32},
                },
            }),
        }
        for name, patcher in patches.items():
            setattr(self, name, patcher.start())
            self.addCleanup(patcher.stop)

    def test_wait_targets_1530_and_sends_confirmed_prices_once(self):
        self.clock.return_value = datetime(2026, 9, 22, 15, 29, 59)
        self.assertEqual(self.scheduler._korea_close_wait_seconds(), 1)
        self.scheduler._check_korea_market_close_alert()
        self.fetch.assert_not_called()
        self.clock.return_value += timedelta(seconds=1)
        self.assertEqual(self.scheduler._korea_close_wait_seconds(), 0)
        self.scheduler._check_korea_market_close_alert()
        self.scheduler._check_korea_market_close_alert()
        self.bot.send_message.assert_called_once()
        self.assertIn("2,550.00", self.bot.send_message.call_args.args[1])
        self.assertEqual(self.scheduler.last_korea_market_close_alert_date, TRADING_DATE)

    def test_pending_close_retries_instead_of_sending_intraday_prices(self):
        report = self.fetch.return_value
        self.fetch.side_effect = [None, report]
        self.scheduler._check_korea_market_close_alert()
        self.bot.send_message.assert_not_called()
        self.assertIsNone(self.scheduler.last_korea_market_close_alert_date)
        self.clock.return_value += timedelta(seconds=10)
        self.scheduler._check_korea_market_close_alert()
        self.bot.send_message.assert_called_once()

    def test_weekend_and_holiday_do_not_fetch_or_send(self):
        for day in (20, 24):
            self.clock.return_value = datetime(2026, 9, day, 15, 30)
            self.scheduler._check_korea_market_close_alert()
        self.fetch.assert_not_called()
        self.bot.send_message.assert_not_called()


if __name__ == "__main__":
    unittest.main()
