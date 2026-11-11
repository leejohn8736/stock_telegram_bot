import time
import threading
from datetime import timedelta
import price_alerts
from concurrent.futures import ThreadPoolExecutor, as_completed
from config import CHECK_INTERVAL, PRICE_CHECK_INTERVAL
import database
import stock_api
import toss_api
import indicators
import predictor
import market_indices
import market_calendar
import weekly_report
import market_events

# 전송 결과 상태
SENT, SKIPPED, FAILED = "sent", "skipped", "failed"


class AlertScheduler:
    def __init__(self, bot_instance):
        self.bot = bot_instance
        self.is_running = False
        self.scheduler_thread = None
        self.price_thread = None
        self.korea_close_thread = None
        self.weekly_events_thread = None
        self._stop_event = threading.Event()
        # 메모리 플래그는 "불필요한 조회를 줄이는 용도"일 뿐입니다.
        # 중복 전송 방지의 기준은 DB(alert_log)이므로 재시작해도 같은 알림이 다시 가지 않습니다.
        self.last_extreme_check_date = None
        self.last_us_market_close_alert_date = None
        self.last_korea_market_close_alert_date = None

    def start(self):
        """Start the background alert scheduler thread."""
        if self.is_running:
            return
        self.is_running = True
        self._stop_event.clear()
        self.price_thread = threading.Thread(
            target=self._run_price_loop, daemon=True, name="price-alerts")
        self.price_thread.start()
        self.scheduler_thread = threading.Thread(target=self._run_loop, daemon=True)
        self.korea_close_thread = threading.Thread(
            target=self._run_korea_close_loop, daemon=True, name="korea-market-close"
        )
        self.korea_close_thread.start()
        self.weekly_events_thread = threading.Thread(
            target=self._run_weekly_events_loop, daemon=True, name="weekly-market-events"
        )
        self.weekly_events_thread.start()
        self.scheduler_thread.start()
        print("Alert Scheduler Thread Started.")

    def stop(self):
        """Stop the background alert scheduler."""
        self.is_running = False
        self._stop_event.set()
        print("Stopping Alert Scheduler...")

    # ================================================================
    # 공통 유틸
    # ================================================================

    @staticmethod
    def _kst_today():
        return time.strftime("%Y-%m-%d", time.gmtime(time.time() + 9 * 60 * 60))

    def _send_alert_with_topic(self, chat_id, text):
        """
        Send message to saved topic, fallback to General if topic fails.
        Returns message_id on success, None on failure.
        """
        topic_id = database.get_chat_topic(chat_id)
        sent = self.bot.send_message(chat_id, text, message_thread_id=topic_id)
        if sent is None:
            if topic_id is not None:
                print(f"Topic {topic_id} failed; retrying without topic (chat: {chat_id})")
                sent = self.bot.send_message(chat_id, text)
            else:
                print(f"Alert delivery failed (chat: {chat_id})")
        return sent

    def _claim_and_send(self, chat_id, text, claim, release):
        """
        중복 방지의 핵심: '기록 먼저, 전송 나중'.
        - claim(): 이력을 새로 기록하면 True. 이미 보냈거나 DB 오류면 False → 전송하지 않음(알림 폭탄 방지)
        - 전송에 실패하면 release()로 기록을 되돌려 다음 주기에 재시도할 수 있게 함
        반환: SENT / SKIPPED / FAILED
        """
        try:
            if not claim():
                return SKIPPED
        except Exception as exc:
            print(f"Alert claim error (chat: {chat_id}): {exc}")
            return SKIPPED

        try:
            sent = self._send_alert_with_topic(chat_id, text)
        except Exception as exc:
            print(f"Alert send error (chat: {chat_id}): {exc}")
            sent = None

        if sent is None:
            try:
                release()
            except Exception as exc:
                print(f"Alert release error (chat: {chat_id}): {exc}")
            return FAILED
        return SENT

    def _market_recipients(self, alert_key, today_str):
        """시장 알림(종목과 무관)을 받을 수 있고, 오늘 아직 못 받은 채팅 목록."""
        chats = sorted({chat_id for chat_id, _ in database.get_all_subscriptions()})
        return [chat_id for chat_id in chats
                if database.should_send_alert(chat_id, is_market_wide=True)
                and not database.has_sent_market_alert(chat_id, alert_key, today_str)]

    def _send_market_alert(self, recipients, alert_key, today_str, text):
        """수신자 전원에게 시장 알림 전송. 결과 리스트를 반환합니다."""
        results = []
        for chat_id in recipients:
            results.append(self._claim_and_send(
                chat_id, text,
                claim=lambda c=chat_id: database.record_market_alert(c, alert_key, today_str),
                release=lambda c=chat_id: database.release_market_alert(c, alert_key, today_str),
            ))
        return results

    # ================================================================
    # 메인 루프들
    # ================================================================

    def _run_loop(self):
        """Main loop for periodic stock indicator checks."""
        if self._stop_event.wait(10):
            return

        while self.is_running:
            try:
                self._check_weekly_report()
                self._check_us_market_close_alert()
                self._check_extreme_market_conditions()
                self._check_index_high_breakouts()

                print("⏳ Periodic stock check sequence initiated...")
                self._check_all_subscribed_stocks()
                print("✅ Periodic stock check complete.")
            except Exception as e:
                print(f"Error in Alert Scheduler Loop: {e}")

            self._stop_event.wait(CHECK_INTERVAL)

    def _run_price_loop(self):
        """Price alerts must not wait for slow reports or technical analysis."""
        while not self._stop_event.is_set():
            started = time.monotonic()
            try:
                self._check_price_alerts()
            except Exception as exc:
                print(f"Error in price alert scheduler: {exc}")
            self._stop_event.wait(max(1, PRICE_CHECK_INTERVAL - (time.monotonic() - started)))

    def _check_price_alerts(self):
        tickers = [ticker for ticker in database.get_unique_tickers()
                   if self._is_ticker_trading_day(ticker)
                   and any(database.should_send_alert(chat, is_important=True)
                           for chat in database.get_subscribers_for_ticker(ticker))]
        if not tickers:
            return
        with ThreadPoolExecutor(max_workers=min(4, len(tickers))) as pool:
            futures = {pool.submit(stock_api.fetch_price_alert_snapshot, t): t for t in tickers}
            for future in as_completed(futures):
                if self._stop_event.is_set():
                    return
                ticker = futures[future]
                try:
                    data = future.result()
                    if data:
                        self._check_price_change_alerts(ticker, data["current_price"],
                            data["currency"], database.get_subscribers_for_ticker(ticker), data)
                except Exception as exc:
                    print(f"Error fetching price alerts for {ticker}: {exc}")

    def _korea_close_wait_seconds(self):
        """KST 15:30까지 대기하되 시스템 시각을 최소 1분마다 재확인합니다."""
        now = market_calendar.get_korea_now()
        if (self.last_korea_market_close_alert_date == now.strftime("%Y-%m-%d")
                or not market_calendar.is_korea_trading_day(now)):
            return 60.0
        close_at = now.replace(hour=15, minute=30, second=0, microsecond=0)
        return min(60.0, max(0.0, (close_at - now).total_seconds()))

    def _run_korea_close_loop(self):
        """일반 종목 스캔과 독립적으로 15:30에 조회하고 미확인 시 10초 뒤 재시도."""
        while not self._stop_event.is_set():
            try:
                delay = self._korea_close_wait_seconds()
                if delay > 0:
                    self._stop_event.wait(delay)
                    continue
                self._check_korea_market_close_alert()
            except Exception as e:
                print(f"Error in Korea market close scheduler: {e}")
            # 네트워크 조회가 진행 중이면 겹쳐서 실행하지 않습니다.
            self._stop_event.wait(10)

    def _weekly_events_wait_seconds(self):
        now = market_calendar.get_korea_now()
        if now.weekday() == 0 and now.hour < 8:
            due = now.replace(hour=8, minute=0, second=0, microsecond=0)
            return min(60.0, (due - now).total_seconds())
        return 60.0

    def _run_weekly_events_loop(self):
        """Independent clock: send at Monday 08:00 KST; retry until midnight."""
        while not self._stop_event.is_set():
            try:
                self._check_weekly_events()
            except Exception as exc:
                print(f"Error in weekly market events scheduler: {exc}")
            self._stop_event.wait(self._weekly_events_wait_seconds())

    # ================================================================
    # 주간 일정 / 주간 리포트
    # ================================================================

    def _check_weekly_events(self):
        now = market_calendar.get_korea_now()
        if now.weekday() != 0 or now.hour < 8:
            return
        week_start = now.date().isoformat()
        recipients = [chat_id for chat_id in sorted({
            chat_id for chat_id, _ in database.get_all_subscriptions()
        }) if database.should_send_alert(chat_id, is_market_wide=True)
            and not database.has_sent_weekly_events(chat_id, week_start)]
        if not recipients:
            return
        report = market_events.format_weekly_events(market_events.fetch_weekly_events(now))
        sent_count = 0
        for chat_id in recipients:
            try:
                result = self._claim_and_send(
                    chat_id, report,
                    claim=lambda c=chat_id: database.record_weekly_events_send(c, week_start),
                    release=lambda c=chat_id: database.release_weekly_events_send(c, week_start),
                )
                if result == SENT:
                    sent_count += 1
            except Exception as exc:
                print(f"Error sending weekly events to {chat_id}: {exc}")
        if sent_count:
            print(f"📅 Weekly market events sent to {sent_count} chat(s).")

    def _check_weekly_report(self):
        """Send weekly market summary on Monday mornings (KST 08:00-09:59)."""
        now_kst = market_calendar.get_korea_now()
        weekday = now_kst.weekday()  # 0=Monday
        hour = now_kst.hour

        # 매주 월요일 아침 08:00~09:59에만 전송
        if weekday != 0:
            return
        if hour < 8 or hour > 9:
            return

        # 리포트는 지난주 월요일을 키로 저장합니다. 조회 전에 수신자를 확인합니다.
        week_start = (now_kst.date() - timedelta(days=7)).isoformat()
        recipients = [chat_id for chat_id in sorted({
            chat_id for chat_id, _ in database.get_all_subscriptions()
        }) if database.should_send_alert(chat_id, is_market_wide=True)
            and not database.has_sent_weekly_report(chat_id, week_start)]
        if not recipients:
            return

        try:
            print("📊 Preparing weekly market report...")

            data = weekly_report.fetch_weekly_report_data()
            if data.get("week_start") != week_start:
                print("Skipping weekly report with an unexpected week_start")
                return

            report_text = weekly_report.format_weekly_report(data)

            sent_count = 0
            for chat_id in recipients:
                try:
                    result = self._claim_and_send(
                        chat_id, report_text,
                        claim=lambda c=chat_id: database.record_weekly_report_send(c, week_start),
                        release=lambda c=chat_id: database.release_weekly_report_send(c, week_start),
                    )
                    if result == SENT:
                        sent_count += 1
                except Exception as exc:
                    print(f"Error sending weekly report to {chat_id}: {exc}")

            if sent_count:
                print(f"✅ Weekly market report sent to {sent_count} chat(s). (Week: {week_start})")

        except Exception as e:
            print(f"Error sending weekly market report: {e}")

    # ================================================================
    # 장 마감 요약 / 극단 조건 / 지수 최고치 (시장 알림)
    # ================================================================

    def _check_us_market_close_alert(self):
        """
        미국장 마감 후 (미국 동부 기준 16:00~17:59) 시장 요약 알림을 전송합니다.
        하루에 한 번만 전송됩니다. (DB 기록 기준: 재시작해도 중복 전송 없음)
        휴장일(주말/공휴일)에는 전송하지 않습니다.
        """
        now_et = market_calendar.get_us_eastern_now()
        today_str = now_et.strftime("%Y-%m-%d")
        hour = now_et.hour

        if self.last_us_market_close_alert_date == today_str:
            return

        # 미국 동부 시간 16:00~17:59 사이에만 전송 (정규장 마감 이후 요약)
        if not (16 <= hour <= 17):
            return

        if not market_calendar.is_us_trading_day(now_et):
            print("📅 오늘은 미국 시장 휴장일입니다. 마감 요약 알림을 건너뜁니다.")
            return

        # 알림 수신 수준 및 오늘 수신 이력을 먼저 확인 (받을 사람이 없으면 조회하지 않음)
        recipients = self._market_recipients("US_CLOSE", today_str)
        if not recipients:
            self.last_us_market_close_alert_date = today_str
            return

        try:
            print("🇺🇸 Preparing US market close summary...")
            data = market_indices.fetch_all_indices()
            report_text = market_indices.format_us_market_close_report(data)

            results = self._send_market_alert(recipients, "US_CLOSE", today_str, report_text)
            if FAILED not in results:
                self.last_us_market_close_alert_date = today_str
            sent = results.count(SENT)
            if sent:
                print(f"✅ US market close summary sent to {sent} chat(s).")

        except Exception as e:
            print(f"Error sending US market close summary: {e}")

    def _check_korea_market_close_alert(self):
        """
        한국장 마감 후 (한국 시간 15:30 이후) 종가 확인 시 시장 요약을 전송합니다.
        하루에 한 번만 전송됩니다. (DB 기록 기준)
        휴장일(주말/한국 공휴일)에는 전송하지 않습니다.
        """
        now_kst = market_calendar.get_korea_now()
        today_str = now_kst.strftime("%Y-%m-%d")
        hour = now_kst.hour

        if self.last_korea_market_close_alert_date == today_str:
            return

        # 종가 반영이 늦어져도 당일에는 다음 주기에 계속 재시도합니다.
        if hour == 15:
            if now_kst.minute < 30:
                return
        elif hour < 15:
            return

        if not market_calendar.is_korea_trading_day(now_kst):
            print("📅 오늘은 한국 시장 휴장일입니다. 마감 요약 알림을 건너뜁니다.")
            return

        recipients = self._market_recipients("KOREA_CLOSE", today_str)
        if not recipients:
            self.last_korea_market_close_alert_date = today_str
            return

        try:
            print("🇰🇷 Preparing Korea market close summary...")

            data = market_indices.fetch_korea_market_close_data(today_str)
            if not data:
                print("Korea market closing prices are not ready; retrying next cycle.")
                return

            report_text = market_indices.format_korea_market_close_report(data)

            results = self._send_market_alert(recipients, "KOREA_CLOSE", today_str, report_text)
            if FAILED not in results:
                self.last_korea_market_close_alert_date = today_str
            sent = results.count(SENT)
            if sent:
                print(f"✅ Korea market close summary sent to {sent} chat(s).")

        except Exception as e:
            print(f"Error sending Korea market close summary: {e}")

    def _check_extreme_market_conditions(self):
        """
        극단적 시장 조건을 체크하고 알림을 전송합니다. (하루 1회, DB 기록 기준)
        휴장일(주말/공휴일)에는 체크하지 않습니다.
        """
        kst_offset = 9 * 60 * 60
        now_kst = time.gmtime(time.time() + kst_offset)
        today_str = time.strftime("%Y-%m-%d", now_kst)
        hour = now_kst.tm_hour

        if self.last_extreme_check_date == today_str:
            return

        # 장 시간 중에만 체크 (한국 시간 9:00~16:00)
        if hour < 9 or hour >= 16:
            return

        if not market_calendar.is_us_trading_day():
            return

        recipients = self._market_recipients("EXTREME", today_str)
        if not recipients:
            self.last_extreme_check_date = today_str
            return

        try:
            data = market_indices.fetch_all_indices()
            extreme_alerts = market_indices.check_extreme_conditions(data)

            if not extreme_alerts:
                return

            alert_text = "<b>🚨 극단적 시장 조건 감지</b>\n"
            alert_text += f"⏱ 시간: <code>{time.strftime('%Y-%m-%d %H:%M', now_kst)}</code>\n"
            alert_text += "━━━━━━━━━━━━━━━━━━━\n\n"
            for alert_type, message in extreme_alerts:
                alert_text += f"{message}\n"

            results = self._send_market_alert(recipients, "EXTREME", today_str, alert_text)
            if FAILED not in results:
                self.last_extreme_check_date = today_str
            sent = results.count(SENT)
            if sent:
                print(f"⚠️ Extreme market alert sent to {sent} chat(s): {len(extreme_alerts)} conditions detected.")

        except Exception as e:
            print(f"Error checking extreme market conditions: {e}")

    def _check_index_high_breakouts(self):
        """
        주요 지수(S&P 500, NASDAQ, DOW, KOSPI, KOSDAQ)의
        역대 최고가 또는 52주 최고가 돌파를 감지하고 알림을 전송합니다.
        하루에 1번만 전송됩니다. 휴장일에는 조회하지 않습니다.
        """
        if not database.get_all_subscriptions():
            return

        now_kst = market_calendar.get_korea_now()
        now_et = market_calendar.get_us_eastern_now()
        if not market_calendar.is_korea_trading_day(now_kst) and not market_calendar.is_us_trading_day(now_et):
            print("📅 오늘은 한국/미국 시장 모두 휴장일입니다. 지수 최고치 조회를 건너뜁니다.")
            return

        today_str = self._kst_today()

        # 받을 사람이 없으면 외부 조회를 하지 않습니다.
        chats = sorted({chat_id for chat_id, _ in database.get_all_subscriptions()})
        recipients = [chat_id for chat_id in chats
                      if database.should_send_alert(chat_id, is_market_wide=True)
                      and not database.has_sent_high_breakout_alert(chat_id, "INDEX", "ALL", today_str)]
        if not recipients:
            return

        try:
            highs_data = market_indices.fetch_all_index_highs()
            if not highs_data:
                return

            current_prices = {}
            indices_data = market_indices.fetch_market_indices()
            for key, idx_data in indices_data.items():
                current_prices[key] = idx_data.get("value")

            korea_data = market_indices.fetch_korea_market_indices()
            for key, idx_data in korea_data.items():
                current_prices[key] = idx_data.get("value")

            breakouts = market_indices.check_index_high_breakouts(highs_data, current_prices)
            if not breakouts:
                return

            alert_text = "<b>🏆 지수 최고치 돌파 알림</b>\n"
            alert_text += f"⏱ 시간: <code>{time.strftime('%Y-%m-%d %H:%M', time.gmtime(time.time() + 9 * 60 * 60))}</code>\n"
            alert_text += "━━━━━━━━━━━━━━━━━━━\n\n"
            for breakout_type, message in breakouts:
                alert_text += f"{message}\n"

            sent = 0
            for chat_id in recipients:
                result = self._claim_and_send(
                    chat_id, alert_text,
                    claim=lambda c=chat_id: database.record_high_breakout_alert(c, "INDEX", "ALL", today_str, 0),
                    release=lambda c=chat_id: database.release_high_breakout_alert(c, "INDEX", "ALL", today_str),
                )
                if result == SENT:
                    sent += 1

            if sent:
                print(f"🏆 Index high breakout alert sent to {sent} chat(s): {len(breakouts)} breakouts detected.")

        except Exception as e:
            print(f"Error checking index high breakouts: {e}")

    # ================================================================
    # 종목별 알림
    # ================================================================

    def _check_stock_high_breakouts(self, ticker, stock_data):
        """
        개별 종목의 역대 최고가 또는 52주 최고가 돌파를 감지하고 알림을 전송합니다.
        동일한 (티커, 유형) 조합은 하루에 1번만 알림을 전송합니다.
        """
        current_price = stock_data.get("current_price")
        if current_price is None or current_price <= 0:
            return

        subscribers = database.get_subscribers_for_ticker(ticker)
        if not subscribers:
            return

        # 개별 종목 중요 알림을 받을 수 있는 채팅이 없으면 조회하지 않습니다.
        eligible = [chat_id for chat_id in subscribers
                    if database.should_send_alert(chat_id, is_important=True)]
        if not eligible:
            return

        today_str = self._kst_today()

        try:
            highs_data = stock_api.fetch_highs_data(ticker)
            if not highs_data:
                return

            stock_name = stock_data.get("name", ticker)
            currency = stock_data.get("currency", "USD")

            # 역대 최고가 돌파 체크
            all_time_high = highs_data.get("all_time_high")
            all_time_high_date = highs_data.get("all_time_high_date")

            if all_time_high is not None and current_price > all_time_high:
                pct_above = ((current_price - all_time_high) / all_time_high) * 100
                alert_text = (
                    f"<b>🏆 [{stock_name}] 역대 최고가 돌파!</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━\n"
                    f"💵 현재가: <b>{current_price:.2f} {currency}</b>\n"
                    f"📈 기존 역대 최고가: {all_time_high:.2f} {currency} ({all_time_high_date or 'N/A'})\n"
                    f"📊 돌파 폭: <b>+{pct_above:.2f}%</b>"
                )

                for chat_id in eligible:
                    self._claim_and_send(
                        chat_id, alert_text,
                        claim=lambda c=chat_id: database.record_high_breakout_alert(c, ticker, "ALL_TIME_HIGH", today_str, current_price),
                        release=lambda c=chat_id: database.release_high_breakout_alert(c, ticker, "ALL_TIME_HIGH", today_str),
                    )

            # 52주 최고가 돌파 체크 (역대 최고가와 다를 때만)
            week52_high = highs_data.get("week52_high")
            week52_high_date = highs_data.get("week52_high_date")

            if week52_high is not None and current_price > week52_high:
                # 역대 최고가도 돌파한 경우에는 52주 알림은 생략 (중복 방지)
                if all_time_high is not None and current_price > all_time_high:
                    return

                pct_above = ((current_price - week52_high) / week52_high) * 100
                alert_text = (
                    f"<b>📈 [{stock_name}] 52주 최고가 돌파!</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━\n"
                    f"💵 현재가: <b>{current_price:.2f} {currency}</b>\n"
                    f"📈 기존 52주 최고가: {week52_high:.2f} {currency} ({week52_high_date or 'N/A'})\n"
                    f"📊 돌파 폭: <b>+{pct_above:.2f}%</b>"
                )

                for chat_id in eligible:
                    self._claim_and_send(
                        chat_id, alert_text,
                        claim=lambda c=chat_id: database.record_high_breakout_alert(c, ticker, "WEEK52_HIGH", today_str, current_price),
                        release=lambda c=chat_id: database.release_high_breakout_alert(c, ticker, "WEEK52_HIGH", today_str),
                    )

        except Exception as e:
            print(f"Error checking high breakouts for {ticker}: {e}")

    def _is_ticker_trading_day(self, ticker):
        """
        티커가 한국 주식(.KS/.KQ)인지 미국 주식인지 판별하여
        해당 시장의 거래일(휴장일 아님)인지 확인합니다.
        """
        ticker_upper = ticker.upper()
        if ticker_upper.endswith(".KS") or ticker_upper.endswith(".KQ"):
            return market_calendar.is_korea_trading_day()
        return market_calendar.is_us_trading_day()

    def _check_all_subscribed_stocks(self):
        """
        Gathers unique tickers, processes indicators, and sends alerts if events trigger.
        휴장일(주말/공휴일)에는 실시간 조회를 하지 않습니다.
        """
        tickers = database.get_unique_tickers()
        if not tickers:
            print("No subscriptions active. Skipping market scan.")
            return

        tickers = [ticker for ticker in tickers if self._is_ticker_trading_day(ticker)]
        if not tickers:
            return

        # 토스증권 Open API 설정 시: 전체 종목의 현재가를 배치 요청 1회로 미리 조회
        price_cache = None
        if toss_api.is_configured():
            price_cache = stock_api.fetch_current_prices_batch(tickers)
            print(f"⚡ Toss OpenAPI: 현재가 {len(price_cache)}/{len(tickers)} 종목 배치 조회 완료")

        for ticker in tickers:
            try:
                stock_data = stock_api.fetch_stock_data(ticker, price_cache=price_cache)
                if not stock_data:
                    print(f"Skipping {ticker} scan - could not retrieve stock data.")
                    continue

                self._check_stock_high_breakouts(ticker, stock_data)
                self._process_ticker_alerts(ticker, stock_data)

                # Yahoo Finance 사용 시에만 API 요청 제한을 피하기 위한 쿨다운 유지
                if not toss_api.is_configured():
                    time.sleep(1)
            except Exception as e:
                print(f"Error processing scan for {ticker}: {e}")

    def _process_ticker_alerts(self, ticker, stock_data):
        """
        Evaluates technical criteria for a single ticker and alerts subscribers.
        (가격 변동 % 알림은 독립 워커가 처리합니다.)
        """
        today_str = self._kst_today()

        closes = stock_data["closes"]
        current_price = stock_data["current_price"]
        currency = stock_data["currency"]

        subscribers = database.get_subscribers_for_ticker(ticker)
        if not subscribers:
            return

        # Price alerts are handled exclusively by the independent price worker.
        if len(closes) < 21:
            return

        # 121개 이상 필요: 120일 이평선 계산 가능
        has_long_sma_data = len(closes) >= 121

        upper_bands, middle_bands, lower_bands = indicators.calculate_bollinger_bands(closes, period=20)
        rsi_list = indicators.calculate_rsi(closes, period=14)
        sma_60_list = indicators.calculate_sma(closes, period=60) if has_long_sma_data else None
        sma_120_list = indicators.calculate_sma(closes, period=120) if has_long_sma_data else None

        price_now = current_price
        price_prev = closes[-2]

        bb_upper_now, bb_middle_now, bb_lower_now = upper_bands[-1], middle_bands[-1], lower_bands[-1]
        bb_upper_prev, bb_middle_prev, bb_lower_prev = upper_bands[-2], middle_bands[-2], lower_bands[-2]

        rsi_now = rsi_list[-1]
        rsi_prev = rsi_list[-2]

        sma60_now = sma_60_list[-1] if sma_60_list else None
        sma60_prev = sma_60_list[-2] if sma_60_list and len(sma_60_list) >= 2 else None
        sma120_now = sma_120_list[-1] if sma_120_list else None
        sma120_prev = sma_120_list[-2] if sma_120_list and len(sma_120_list) >= 2 else None

        if None in (bb_upper_now, bb_middle_now, bb_lower_now, bb_upper_prev, bb_middle_prev, bb_lower_prev, rsi_now, rsi_prev):
            return

        events = {}

        # 1. Bollinger Band Lower Breach
        if price_prev > bb_lower_prev and price_now <= bb_lower_now:
            events["BB_LOWER_BREACH"] = {
                "title": "⚡ 볼린저 밴드 하단 이탈 (매수 찬스)",
                "msg": f"현재가(<b>{price_now:.2f} {currency}</b>)가 볼린저 밴드 하단선(<b>{bb_lower_now:.2f}</b>)을 하향 돌파했습니다. 과매도 반등 가능성이 있습니다.",
                "type": "BB_LOWER"
            }
        elif price_now > bb_lower_now:
            self._clear_event_for_all(subscribers, ticker, "BB_LOWER")

        # 2. Bollinger Band Upper Breach
        if price_prev < bb_upper_prev and price_now >= bb_upper_now:
            events["BB_UPPER_BREACH"] = {
                "title": "⚠️ 볼린저 밴드 상단 돌파 (과열 경보)",
                "msg": f"현재가(<b>{price_now:.2f} {currency}</b>)가 볼린저 밴드 상단선(<b>{bb_upper_now:.2f}</b>)을 상향 돌파했습니다. 단기 과열로 조정 가능성이 있습니다.",
                "type": "BB_UPPER"
            }
        elif price_now < bb_upper_now:
            self._clear_event_for_all(subscribers, ticker, "BB_UPPER")

        # 3. SMA 20 Cross Under
        if price_prev >= bb_middle_prev and price_now < bb_middle_now:
            events["SMA_20_CROSS_UNDER"] = {
                "title": "📉 20일 이동평균선 이탈 (추세 하락 우려)",
                "msg": f"현재가(<b>{price_now:.2f} {currency}</b>)가 20일선(<b>{bb_middle_now:.2f}</b>)을 아래로 뚫고 내려왔습니다. 지지선 하향 이탈에 주의하세요.",
                "type": "SMA_20_UNDER"
            }
        elif price_now >= bb_middle_now:
            self._clear_event_for_all(subscribers, ticker, "SMA_20_UNDER")

        # 4. SMA 20 Cross Over
        if price_prev <= bb_middle_prev and price_now > bb_middle_now:
            events["SMA_20_CROSS_OVER"] = {
                "title": "📈 20일 이동평균선 회복 (추세 상승 전환)",
                "msg": f"현재가(<b>{price_now:.2f} {currency}</b>)가 20일선(<b>{bb_middle_now:.2f}</b>) 위로 올라섰습니다. 단기 상승 추세로 전환될 수 있습니다.",
                "type": "SMA_20_OVER"
            }
        elif price_now <= bb_middle_now:
            self._clear_event_for_all(subscribers, ticker, "SMA_20_OVER")

        # 5. RSI Oversold Entrance
        if rsi_prev > 30 and rsi_now <= 30:
            events["RSI_OVERSOLD"] = {
                "title": "⚡ RSI 과매도 진입 (반등 대기)",
                "msg": f"RSI 지표가 <b>{rsi_now:.1f}</b>로 과매도 기준선(30 이하)에 진입했습니다. 기술적 반등 유입을 기대해 볼 수 있습니다.",
                "type": "RSI_OVERSOLD"
            }
        elif rsi_now > 30:
            self._clear_event_for_all(subscribers, ticker, "RSI_OVERSOLD")

        # 6. RSI Overbought Entrance
        if rsi_prev < 70 and rsi_now >= 70:
            events["RSI_OVERBOUGHT"] = {
                "title": "⚠️ RSI 과매수 진입 (추가 상승 주의)",
                "msg": f"RSI 지표가 <b>{rsi_now:.1f}</b>로 과매수 기준선(70 이상)에 진입했습니다. 고점 매도 매물이 출현할 위험이 큽니다.",
                "type": "RSI_OVERBOUGHT"
            }
        elif rsi_now < 70:
            self._clear_event_for_all(subscribers, ticker, "RSI_OVERBOUGHT")

        # 7. SMA 60 Cross Under
        if sma60_now is not None and sma60_prev is not None:
            if price_prev >= sma60_prev and price_now < sma60_now:
                events["SMA_60_CROSS_UNDER"] = {
                    "title": "📉 60일 이동평균선 이탈 (중기 추세 약화)",
                    "msg": f"현재가(<b>{price_now:.2f} {currency}</b>)가 60일선(<b>{sma60_now:.2f}</b>)을 아래로 뚫고 내려왔습니다. 중기 지지선 이탈로 추세가 약화될 수 있습니다.",
                    "type": "SMA_60_UNDER"
                }
            elif price_now >= sma60_now:
                self._clear_event_for_all(subscribers, ticker, "SMA_60_UNDER")

        # 8. SMA 60 Cross Over
        if sma60_now is not None and sma60_prev is not None:
            if price_prev <= sma60_prev and price_now > sma60_now:
                events["SMA_60_CROSS_OVER"] = {
                    "title": "📈 60일 이동평균선 회복 (중기 추세 강화)",
                    "msg": f"현재가(<b>{price_now:.2f} {currency}</b>)가 60일선(<b>{sma60_now:.2f}</b>) 위로 올라섰습니다. 중기 상승 추세로 전환될 수 있습니다.",
                    "type": "SMA_60_OVER"
                }
            elif price_now <= sma60_now:
                self._clear_event_for_all(subscribers, ticker, "SMA_60_OVER")

        # 9. SMA 120 Cross Under
        if sma120_now is not None and sma120_prev is not None:
            if price_prev >= sma120_prev and price_now < sma120_now:
                events["SMA_120_CROSS_UNDER"] = {
                    "title": "📉 120일 이동평균선 이탈 (장기 추세 약화)",
                    "msg": f"현재가(<b>{price_now:.2f} {currency}</b>)가 120일선(<b>{sma120_now:.2f}</b>)을 아래로 뚫고 내려왔습니다. 장기 지지선 이탈로 큰 조정이 올 수 있습니다.",
                    "type": "SMA_120_UNDER"
                }
            elif price_now >= sma120_now:
                self._clear_event_for_all(subscribers, ticker, "SMA_120_UNDER")

        # 10. SMA 120 Cross Over
        if sma120_now is not None and sma120_prev is not None:
            if price_prev <= sma120_prev and price_now > sma120_now:
                events["SMA_120_CROSS_OVER"] = {
                    "title": "📈 120일 이동평균선 회복 (장기 추세 강화)",
                    "msg": f"현재가(<b>{price_now:.2f} {currency}</b>)가 120일선(<b>{sma120_now:.2f}</b>) 위로 올라섰습니다. 장기 상승 추세로 전환될 수 있습니다.",
                    "type": "SMA_120_OVER"
                }
            elif price_now <= sma120_now:
                self._clear_event_for_all(subscribers, ticker, "SMA_120_OVER")

        stock_name = stock_data.get("name", ticker)

        for event_info in events.values():
            sig_type = event_info["type"]
            for chat_id in subscribers:
                # 일반 기술적 신호는 '모든 알람(ALL)' 수준에서만 수신
                if not database.should_send_alert(chat_id):
                    continue

                # 이미 이 신호 사이클을 알렸다면 스킵 (조건이 정상화되어 clear 될 때까지)
                if database.get_last_signal(chat_id, ticker, sig_type) is not None:
                    continue

                alert_text = (
                    f"<b>🔔 {event_info['title']}</b>\n"
                    f"종목: <b>{stock_name}</b> ({ticker})\n"
                    f"시간: <code>{time.strftime('%Y-%m-%d %H:%M:%S')}</code>\n\n"
                    f"{event_info['msg']}"
                )

                # 하루 1회 제한: 기록 먼저 → 전송. 실패하면 기록 되돌림
                result = self._claim_and_send(
                    chat_id, alert_text,
                    claim=lambda c=chat_id, s=sig_type: database.record_signal_alert(c, ticker, s, today_str, price_now),
                    release=lambda c=chat_id, s=sig_type: database.release_signal_alert(c, ticker, s, today_str),
                )
                if result == SENT:
                    database.set_last_signal(chat_id, ticker, sig_type, price_now)

        # --- 매수/매도 권장 알림 (STRONG BUY / STRONG SELL) ---
        self._check_recommendation_alerts(ticker, stock_data, subscribers)

    def _check_recommendation_alerts(self, ticker, stock_data, subscribers):
        """
        STRONG BUY / STRONG SELL 신호를 감지하여 권장 가격과 함께 알림을 전송합니다.
        유형(STRONG_BUY/STRONG_SELL)별로 하루 1회만 전송됩니다.
        """
        try:
            # 알림을 받을 수 있는 채팅이 없으면 분석하지 않습니다.
            eligible = [chat_id for chat_id in subscribers
                        if database.should_send_alert(chat_id, is_important=True)]
            if not eligible:
                return

            analysis = predictor.predict_buy_sell_prices(stock_data)
            if "error" in analysis:
                return

            recommendation = analysis["recommendation"]
            if recommendation not in ("STRONG BUY", "STRONG SELL"):
                return

            current_price = analysis["current_price"]
            buy_target = analysis["buy_target"]
            sell_target = analysis["sell_target"]
            stop_loss = analysis.get("stop_loss", 0)
            confidence = analysis["confidence"]
            currency = analysis["currency"]
            stock_name = stock_data.get("name", ticker)

            today_str = self._kst_today()

            if recommendation == "STRONG BUY":
                alert_type = "STRONG_BUY"
                emoji = "🟢🔥"
                title = "무조건 매수 권장!"
                action_desc = (
                    f"🎯 <b>권장 매수 가격:</b> <code>{buy_target:.2f} {currency}</code> 이하\n"
                    f"<i>(현재가 {current_price:.2f} {currency} 대비 {(current_price - buy_target) / current_price * 100:.1f}% 하락 시 매수 기회)</i>\n"
                )
                if stop_loss > 0:
                    action_desc += f"🛑 <b>손절가:</b> <code>{stop_loss:.2f} {currency}</code>\n"
            else:  # STRONG SELL
                alert_type = "STRONG_SELL"
                emoji = "🔴🔥"
                title = "무조건 매도 권장!"
                action_desc = (
                    f"🎯 <b>권장 매도 가격:</b> <code>{sell_target:.2f} {currency}</code> 이상\n"
                    f"<i>(현재가 {current_price:.2f} {currency} 대비 {(sell_target - current_price) / current_price * 100:.1f}% 상승 시 매도 기회)</i>\n"
                )

            signals = analysis.get("signals", [])
            signal_summary = ""
            if signals:
                signal_summary = "\n".join(f"• {sig}" for sig in signals[:3])

            alert_text = (
                f"<b>{emoji} [{stock_name}] ({ticker}) {title}</b>\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"💵 현재가: <b>{current_price:.2f} {currency}</b>\n"
                f"📢 추천 등급: <b>{emoji} {recommendation}</b>\n"
                f"🎯 예측 신뢰도: <b>{confidence}%</b>\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"{action_desc}"
            )

            if signal_summary:
                alert_text += (
                    f"━━━━━━━━━━━━━━━━━━━\n"
                    f"<b>🔍 판단 근거</b>\n"
                    f"{signal_summary}\n"
                )

            sent_count = 0
            for chat_id in eligible:
                # 유형당 하루 1회: 기록 먼저 → 전송. 실패하면 기록 되돌림
                result = self._claim_and_send(
                    chat_id, alert_text,
                    claim=lambda c=chat_id: database.record_recommendation_alert(c, ticker, today_str, alert_type, current_price),
                    release=lambda c=chat_id: database.release_recommendation_alert(c, ticker, today_str, alert_type),
                )
                if result == SENT:
                    sent_count += 1

            # 실제로 전송된 경우에만 로그를 남깁니다. (예전에는 아무도 못 받아도 'sent'로 찍혔음)
            if sent_count:
                print(f"📢 Recommendation alert sent for {ticker}: {recommendation} ({sent_count} chat(s))")

        except Exception as e:
            print(f"Error checking recommendation alerts for {ticker}: {e}")

    def _check_price_change_alerts(self, ticker, current_price, currency, subscribers, stock_data):
        price_alerts.check_price_changes(self, ticker, current_price, currency, subscribers, stock_data)

    def _clear_event_for_all(self, subscribers, ticker, sig_type):
        """
        Clears the registered alert state for subscribers when conditions normalize.
        This enables them to receive alerts again when conditions are triggered in the future.
        """
        for chat_id in subscribers:
            database.clear_last_signal(chat_id, ticker, sig_type)
