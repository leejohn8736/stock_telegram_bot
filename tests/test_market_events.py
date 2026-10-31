import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import database
import market_events
from scheduler import AlertScheduler


class CalendarTests(unittest.TestCase):
    def titles(self, day, market=None):
        events = market_events.fetch_weekly_events(datetime.fromisoformat(day))["events"]
        return [(e["date"], e["title"]) for e in events if market is None or e["market"] == market]

    def test_current_week(self):
        events = self.titles("2026-09-07")
        self.assertIn(("2026-09-07", "휴장 · 노동절"), events)
        self.assertIn(("2026-09-10", "선물·옵션 동시만기 (네 마녀의 날)"), events)

    def test_us_juneteenth_expiry_moves_to_thursday(self):
        events = self.titles("2026-06-15", "US")
        self.assertTrue(any(d == "2026-06-18" and "앞당김" in t for d, t in events))
        self.assertIn(("2026-06-19", "휴장 · 준틴스"), events)

    def test_korean_lunar_substitutes(self):
        self.assertIn(("2026-05-25", "휴장 · 부처님오신날 대체공휴일"), self.titles("2026-05-25", "KR"))
        self.assertFalse(any(d == "2026-09-28" and "휴장" in t for d, t in self.titles("2026-09-28", "KR")))
        self.assertFalse(any("현충일" in t for _, t in self.titles("2026-06-08", "KR")))

    def test_election_and_year_end(self):
        self.assertIn(("2026-06-03", "휴장 · 전국동시지방선거"), self.titles("2026-06-01", "KR"))
        events = self.titles("2026-12-28")
        self.assertIn(("2026-12-31", "휴장 · 연말 휴장"), events)
        self.assertIn(("2027-01-01", "휴장 · 신정"), events)

    def test_early_closes_and_kst(self):
        self.assertTrue(any("11/28 03:00" in t for _, t in self.titles("2026-11-23", "US")))
        self.assertTrue(any("07/04 02:00" in t for _, t in self.titles("2025-06-30", "US")))
        self.assertFalse(any("조기폐장" in t for _, t in self.titles("2026-06-29", "US")))

    def test_saturday_new_year_does_not_close_friday(self):
        self.assertNotIn(date(2021, 12, 31), market_events.holidays_for_year("US", 2021, []))

    def test_unsupported_year_is_visible(self):
        data = market_events.fetch_weekly_events(date(2030, 9, 2))
        self.assertTrue(data["warnings"])
        self.assertIn("업데이트 필요", market_events.format_weekly_events(data))
        self.assertTrue(any(e["market"] == "US" for e in data["events"]))

    def test_empty_week_and_timezone(self):
        data = market_events.fetch_weekly_events(datetime(2026, 9, 6, 23, tzinfo=timezone.utc))
        self.assertEqual(data["week_start"], "2026-09-07")
        self.assertIn("일정이 없습니다", market_events.format_weekly_events(
            market_events.fetch_weekly_events(date(2026, 8, 24))))

    def test_html_escape(self):
        data = {"week_start": "2026-09-07", "week_end": "2026-09-13", "warnings": [],
                "events": [{"date": "2026-09-07", "market": "KR", "title": "<test> &"}]}
        self.assertIn("&lt;test&gt; &amp;", market_events.format_weekly_events(data))


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db_patch = patch.object(database, "DB_PATH", str(Path(self.temp.name) / "test.db"))
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        database.init_db()
        database.add_subscription(1, "AAPL")
        database.add_subscription(1, "MSFT")
        database.add_subscription(2, "SPY")
        self.bot = Mock()
        self.bot.send_message.return_value = 123
        self.scheduler = AlertScheduler(self.bot)
        self.now = patch("market_calendar.get_korea_now", return_value=datetime(2026, 9, 7, 8))
        self.clock = self.now.start()
        self.addCleanup(self.now.stop)

    def test_once_per_chat_persists_across_restart_and_separate_report(self):
        database.record_weekly_report_send(1, "2026-09-07")
        self.scheduler._check_weekly_events()
        AlertScheduler(self.bot)._check_weekly_events()
        self.assertEqual(self.bot.send_message.call_count, 2)
        self.assertTrue(database.has_sent_weekly_events(1, "2026-09-07"))

    def test_schedule_gate_and_late_start(self):
        for now in (datetime(2026, 9, 7, 7, 59), datetime(2026, 9, 8, 8)):
            self.clock.return_value = now
            self.scheduler._check_weekly_events()
        self.bot.send_message.assert_not_called()
        self.clock.return_value = datetime(2026, 9, 7, 18)
        self.scheduler._check_weekly_events()
        self.assertEqual(self.bot.send_message.call_count, 2)

    def test_failure_retries_only_unsent_chat(self):
        self.bot.send_message.side_effect = [None, 123, 124]
        self.scheduler._check_weekly_events()
        self.assertFalse(database.has_sent_weekly_events(1, "2026-09-07"))
        self.assertTrue(database.has_sent_weekly_events(2, "2026-09-07"))
        self.scheduler._check_weekly_events()
        self.assertEqual(self.bot.send_message.call_count, 3)

    def test_off_and_topic_fallback(self):
        database.set_chat_alert_level(2, "OFF")
        database.set_chat_topic(1, 77)
        self.bot.send_message.side_effect = [None, 123]
        self.scheduler._check_weekly_events()
        self.assertEqual(self.bot.send_message.call_count, 2)
        self.assertEqual(self.bot.send_message.call_args_list[0].kwargs["message_thread_id"], 77)
        self.assertNotIn("message_thread_id", self.bot.send_message.call_args_list[1].kwargs)
        self.assertFalse(database.has_sent_weekly_events(2, "2026-09-07"))

    def test_wait_targets_eight_and_collection_skipped_when_done(self):
        self.clock.return_value = datetime(2026, 9, 7, 7, 59, 55)
        self.assertEqual(self.scheduler._weekly_events_wait_seconds(), 5)
        self.clock.return_value += timedelta(seconds=5)
        self.scheduler._check_weekly_events()
        with patch("market_events.fetch_weekly_events") as fetch:
            self.scheduler._check_weekly_events()
            fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
