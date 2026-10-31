"""
Weekly market summary report module.
- Major indices (US: S&P 500, NASDAQ, DOW / Korea: KOSPI, KOSDAQ) weekly changes
- Subscribed stocks weekly changes
- Fear & Greed Index, VIX reference info
"""

import time
import html
from concurrent.futures import ThreadPoolExecutor

import database
import stock_api
import market_indices


def _get_week_range():
    """
    지난주(월~금) 날짜 범위를 계산합니다. (KST 기준)
    반환: (week_start, week_end) - "YYYY-MM-DD" 형식
    """
    now_kst = time.gmtime(time.time() + 9 * 60 * 60)
    today_weekday = now_kst.tm_wday  # 0=월요일

    # 이번 주 월요일 (KST)
    this_monday_ts = time.time() - (today_weekday * 86400)

    # 지난주 월요일~금요일
    last_monday_ts = this_monday_ts - 7 * 86400
    last_friday_ts = this_monday_ts - 3 * 86400

    week_start = time.strftime("%Y-%m-%d", time.gmtime(last_monday_ts + 9 * 60 * 60))
    week_end = time.strftime("%Y-%m-%d", time.gmtime(last_friday_ts + 9 * 60 * 60))

    return week_start, week_end


def fetch_weekly_report_data():
    """
    주간 리포트에 필요한 모든 데이터를 수집합니다.

    반환: {
        indices: { sp500: {...}, nasdaq: {...}, dow: {...}, kospi: {...}, kosdaq: {...} },
        stocks: [ { ticker, name, currency, value, week_change, week_change_pct }, ... ],
        fear_greed: {...},
        vix: {...},
        week_start: "YYYY-MM-DD",
        week_end: "YYYY-MM-DD",
        timestamp: "..."
    }
    """
    week_start, week_end = _get_week_range()

    result = {
        "indices": {},
        "stocks": [],
        "fear_greed": None,
        "vix": None,
        "week_start": week_start,
        "week_end": week_end,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() + 9 * 60 * 60))
    }

    # 1. 주요 지수 주간 변동
    try:
        result["indices"] = market_indices.fetch_weekly_indices_data()
    except Exception as e:
        print(f"Error fetching weekly indices data: {e}")

    # 2. 관심 종목 주간 변동 (병렬 조회)
    try:
        # 모든 구독자의 고유 티커 목록
        tickers = database.get_unique_tickers()

        def _fetch_stock_weekly(ticker):
            try:
                return stock_api.fetch_weekly_change(ticker)
            except Exception as e:
                print(f"Error fetching weekly change for {ticker}: {e}")
                return None

        with ThreadPoolExecutor(max_workers=min(8, max(1, len(tickers)))) as executor:
            for weekly in executor.map(_fetch_stock_weekly, tickers):
                if weekly:
                    result["stocks"].append(weekly)
    except Exception as e:
        print(f"Error fetching subscribed stocks weekly data: {e}")

    # 3. 참고 정보 (공포탐욕지수, VIX)
    try:
        result["fear_greed"] = market_indices.fetch_fear_greed_index()
        result["vix"] = market_indices.fetch_vix()
    except Exception as e:
        print(f"Error fetching reference data: {e}")

    return result


def format_weekly_report(data):
    """
    주간 시장 요약 리포트를 HTML 포맷으로 변환합니다.

    data: fetch_weekly_report_data() 결과
    """
    lines = []
    lines.append("<b>📊 주간 시장 요약 리포트</b>")
    lines.append(f"📅 <code>{data.get('week_start', '')} ~ {data.get('week_end', '')}</code>")
    lines.append(f"⏱ 생성시간: <code>{data.get('timestamp', '')}</code>")
    lines.append("━━━━━━━━━━━━━━━━━━━")

    # 주요 지수 주간 변동
    indices = data.get("indices", {})
    if indices:
        lines.append("\n<b>📈 주요 지수 주간 변동</b>")
        for key in ["sp500", "nasdaq", "dow", "kospi", "kosdaq"]:
            idx = indices.get(key)
            if idx:
                name = idx.get("name", key)
                value = idx.get("value", 0)
                week_change = idx.get("week_change", 0)
                week_change_pct = idx.get("week_change_pct", 0)

                emoji = "🟢" if week_change_pct > 0 else "🔴" if week_change_pct < 0 else "⚪"
                sign = "+" if week_change_pct > 0 else ""

                lines.append(f"• {emoji} <b>{name}</b>: {value:,.2f} ({sign}{week_change_pct:.2f}% · {sign}{week_change:,.2f})")

    # 관심 종목 주간 변동
    stocks = data.get("stocks", [])
    if stocks:
        lines.append("\n<b>⭐ 관심 종목 주간 변동</b>")
        for stock in stocks:
            ticker = stock.get("ticker", "")
            name = stock.get("name", ticker)
            currency = stock.get("currency", "USD")
            value = stock.get("week_end_price", 0)
            week_change = stock.get("change", 0)
            week_change_pct = stock.get("change_pct", 0)

            emoji = "🟢" if week_change_pct > 0 else "🔴" if week_change_pct < 0 else "⚪"
            sign = "+" if week_change_pct > 0 else ""

            lines.append(f"• {emoji} <b>{html.escape(name)}</b> ({ticker}): {value:,.2f} {currency} ({sign}{week_change_pct:.2f}% · {sign}{week_change:,.2f})")

    # 참고 정보
    lines.append("\n━━━━━━━━━━━━━━━━━━━")
    lines.append("<b>🌐 참고 정보</b>")

    fg = data.get("fear_greed")
    if fg and fg.get("value") is not None:
        value = fg["value"]
        classification = fg.get("classification", "")
        week_ago = fg.get("week_ago")
        week_change_str = ""
        if week_ago:
            week_change = value - week_ago
            week_change_str = f" (1주 전 대비 {week_change:+.1f})"
        lines.append(f"• 🎭 공포탐욕지수: <b>{value:.1f}</b> ({classification}){week_change_str}")

    vix = data.get("vix")
    if vix:
        value = vix["value"]
        change_pct = vix.get("change_pct", 0)
        lines.append(f"• 📊 VIX: {value:.2f} ({change_pct:+.2f}%)")


    return "\n".join(lines)
