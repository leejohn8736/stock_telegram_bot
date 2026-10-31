"""Session-scoped percentage alerts, independent from technical analysis."""
import html
import math
import time

import database
import toss_api


def check_price_changes(scheduler, ticker, current_price, currency, subscribers, data):
    def positive(value):
        return isinstance(value, (int, float)) and math.isfinite(value) and value > 0

    previous = data.get("previous_close")
    if not positive(previous) or not positive(current_price):
        return
    session = data.get("session_date") or toss_api._market_local_date_str(time.time(), ticker)
    current_change = (current_price / previous - 1) * 100
    extremes = {"up": data.get("session_high", current_price),
                "down": data.get("session_low", current_price)}
    for chat_id in subscribers:
        if not database.should_send_alert(chat_id, is_important=True):
            continue
        try:
            sent = database.get_daily_alerts_for_date(chat_id, ticker, session)
            for direction, extreme in extremes.items():
                if not positive(extreme):
                    continue
                change = (extreme / previous - 1) * 100
                magnitude = change if direction == "up" else -change
                threshold = next((t for t in (20, 10, 5) if magnitude >= t - 1e-9), None)
                already = max((a["threshold_pct"] for a in sent
                               if a["direction"] == direction), default=0)
                if threshold is None or threshold <= already:
                    continue
                label = "상승" if direction == "up" else "하락"
                point = "고가" if direction == "up" else "저가"
                name = html.escape(str(data.get("name", ticker)))
                unit = html.escape(str(currency))
                pct_str = f"{magnitude:.2f}".rstrip("0").rstrip(".")
                text = (
                    f"<b>🔔 [{name}] {label} {pct_str}% 도달</b>\n"
                    f"종목: {html.escape(ticker)} · 거래일: {session}\n"
                    f"📌 전일 정규장 종가: {previous:.2f} {unit}\n"
                    f"📊 당일 {point}: <b>{extreme:.2f} {unit} ({change:+.2f}%)</b>\n"
                    f"💵 최근 확인가: <b>{current_price:.2f} {unit} ({current_change:+.2f}%)</b>\n"
                    "<i>프리·애프터장 포함, 당일 분봉 기준. 조회 사이에 지나간 도달도 안내합니다.</i>"
                )
                timestamp = data.get("quote_timestamp")
                if timestamp is not None:
                    checked = time.strftime('%m/%d %H:%M', time.gmtime(timestamp + 9 * 3600))
                    text += f"\n시세 시각: {checked} KST"
                if scheduler._send_alert_with_topic(chat_id, text) is None:
                    continue
                database.record_daily_alert(chat_id, ticker, session, threshold, direction)
                database.set_last_price(chat_id, ticker, current_price)
        except Exception as exc:
            print(f"Error checking price change for {ticker} (chat {chat_id}): {exc}")
