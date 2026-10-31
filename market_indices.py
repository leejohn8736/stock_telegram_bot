"""
Market indices data collection module.
- CNN Fear & Greed Index
- VIX, S&P 500, NASDAQ, DOW
- USD/KRW exchange rate
- US 10Y Treasury yield
- US Dollar Index (DXY)
"""

import urllib.request
import urllib.parse
import json
import ssl
import time
import threading
import math
from datetime import datetime, timedelta, timezone
import market_calendar
import market_events
from concurrent.futures import ThreadPoolExecutor, as_completed

# 요청 기본값 (실패 시에도 빠르게 포기하도록 타임아웃/재시도를 짧게 유지)
_REQUEST_TIMEOUT = 10   # seconds
_REQUEST_RETRIES = 2
_REQUEST_DELAY = 1      # seconds

# 동시 요청 상한 (Yahoo Finance rate limit 보호)
_MAX_CONCURRENT_REQUESTS = 8

# 집계 함수(fetch_all_indices 등) 결과의 짧은 TTL 캐시 (연속 조회 시 중복 요청 방지)
_CACHE_TTL = 60  # seconds
_result_cache = {}
_result_cache_lock = threading.Lock()


def _make_request(url, headers=None, retries=_REQUEST_RETRIES, delay=_REQUEST_DELAY, timeout=_REQUEST_TIMEOUT):
    """HTTP request helper with retry logic."""
    if headers is None:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
        }
    
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, context=ctx, timeout=timeout) as response:
                if response.status == 200:
                    return response.read().decode("utf-8")
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(delay)
                continue
            print(f"Request failed for {url}: {e}")
    return None


def _run_concurrently(tasks, max_workers=_MAX_CONCURRENT_REQUESTS):
    """
    여러 네트워크 조회 함수를 스레드 풀로 동시에 실행합니다.

    tasks: { key: callable } — 각 callable은 인자 없이 호출됩니다.
    반환: { key: 결과 } — 작업이 예외를 던지면 해당 key의 값은 None.
    (개별 fetch 함수들이 내부에서 예외를 처리하므로 None은 대체로 조회 실패를 의미)
    """
    results = {}
    if not tasks:
        return results

    workers = max(1, min(len(tasks), max_workers or len(tasks)))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_key = {executor.submit(fn): key for key, fn in tasks.items()}
        for future in as_completed(future_to_key):
            key = future_to_key[future]
            try:
                results[key] = future.result()
            except Exception as e:
                print(f"Error in concurrent fetch '{key}': {e}")
                results[key] = None

    return results


def _cached(key, fetch_fn):
    """
    간단한 TTL 캐시 (_CACHE_TTL 초 이내의 반복 조회는 즉시 반환).
    조회 실패(None)는 캐시하지 않으므로 다음 호출에서 재시도됩니다.
    """
    now = time.time()
    with _result_cache_lock:
        hit = _result_cache.get(key)
        if hit is not None and now - hit[0] < _CACHE_TTL:
            return hit[1]

    value = fetch_fn()
    if value is not None:
        with _result_cache_lock:
            _result_cache[key] = (now, value)

    return value


def _fetch_yahoo_quote(symbol, label, digits=2, change_digits=2):
    """
    Yahoo Finance chart API에서 단일 시세(지수/환율 등)를 조회합니다.
    range=10d&interval=1d 데이터에서 현재가(regularMarketPrice)와 전일 종가를 추출해
    등락을 계산합니다. fetch_vix 등 동일 패턴 함수들과 fetch_usd_krw(폴백)의 공통 구현입니다.

    digits: value/previous_close 반올림 자릿수 (예: 국채 수익률 3)
    change_digits: change 반올림 자릿수 (예: 환율 4)
    반환: { value, change, change_pct, previous_close } 또는 None
    """
    encoded_symbol = urllib.parse.quote(symbol)
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded_symbol}"
        f"?range=10d&interval=1d&includePrePost=true"
    )

    try:
        response = _make_request(url)
        if response:
            data = json.loads(response)
            chart_result = data.get("chart", {}).get("result", [{}])[0]
            meta = chart_result.get("meta", {})

            current_price = meta.get("regularMarketPrice")

            # 전일 종가: daily OHLC 데이터에서 직접 추출 (meta.chartPreviousClose는 range=10d일 때 부정확)
            previous_close = _extract_previous_close_from_daily(chart_result)
            if previous_close is None:
                previous_close = meta.get("chartPreviousClose") or meta.get("previousClose")

            if current_price and previous_close:
                change = current_price - previous_close
                change_pct = (change / previous_close) * 100 if previous_close != 0 else 0

                return {
                    "value": round(current_price, digits),
                    "change": round(change, change_digits),
                    "change_pct": round(change_pct, 2),
                    "previous_close": round(previous_close, digits)
                }
    except Exception as e:
        print(f"Error fetching {label}: {e}")

    return None


def fetch_fear_greed_index():
    """
    Fetch Fear & Greed Index from CNN Money API.
    Returns: { value, classification, previous_close, week_ago, month_ago }
    """
    url = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://money.cnn.com/",
        "Accept": "application/json"
    }
    
    try:
        response = _make_request(url, headers=headers)
        if response:
            data = json.loads(response)
            fg = data.get("fear_and_greed", {})
            
            if not fg or fg.get("score") is None:
                return None
            
            score = float(fg["score"])
            rating_en = fg.get("rating", "")
            
            # 분류 매핑
            classification_map = {
                "extreme fear": "극도 공포",
                "fear": "공포",
                "neutral": "중립",
                "greed": "탐욕",
                "extreme greed": "극도 탐욕"
            }
            
            classification = classification_map.get(rating_en.lower(), rating_en)
            
            # 이전 값들
            previous_close = fg.get("previous_close")
            week_ago = fg.get("previous_1_week")
            month_ago = fg.get("previous_1_month")
            
            return {
                "value": round(score, 1),
                "classification": classification,
                "rating_en": rating_en,
                "previous_close": round(previous_close, 1) if previous_close else None,
                "week_ago": round(week_ago, 1) if week_ago else None,
                "month_ago": round(month_ago, 1) if month_ago else None
            }
    except Exception as e:
        print(f"Error fetching Fear & Greed Index: {e}")
    
    return None


def _extract_previous_close_from_daily(chart_result):
    """
    Yahoo Finance chart API 결과에서 daily OHLC 데이터를 기반으로
    실제 전일 종가를 추출합니다.

    meta.chartPreviousClose는 range=10d로 요청 시 range 시작 이전 종가(약 며칠 전)를
    반환할 수 있어 부적합합니다. 또한 includePrePost=true 응답에서는 일부 캔들의 close가
    None 으로 섞여 있고, 조회 시점(장 전/장중/장마감)에 따라 현재가(regularMarketPrice)에
    해당하는 캔들의 위치가 달라질 수 있습니다.

    따라서 고정 인덱스(예: [-1], [-2])를 사용하지 않고 아래 우선순위로 동적으로 계산합니다.
      1. 현재가(regularMarketPrice)와 일치하는 캔들의 직전 유효 close
         (조회 시점이 달라도 항상 '현재 세션 이전 종가'를 정확히 찾음)
      2. 마지막 날짜 이전 날짜의 마지막 유효 close (타임스탬프 기반, stock_api.py와 동일 패턴)
      3. 유효 close가 1개뿐이면 그 값을 반환
    """
    try:
        quote = chart_result.get("indicators", {}).get("quote", [{}])[0]
        closes = quote.get("close", []) or []
        timestamps = chart_result.get("timestamp", []) or []
        meta = chart_result.get("meta", {})
        current_price = meta.get("regularMarketPrice")

        # 정렬된 (timestamp, close) 유효 페어만 추출 (None 제외)
        pairs = []
        for ts, c in zip(timestamps, closes):
            if c is None:
                continue
            pairs.append((ts, c))

        if not pairs:
            return None

        # 1순위: 현재가와 일치하는 캔들의 직전 유효 close
        if current_price is not None:
            tolerance = max(1e-6, abs(current_price) * 0.001)
            match_idx = None
            for i, (_, c) in enumerate(pairs):
                if abs(c - current_price) <= tolerance:
                    match_idx = i  # 마지막 일치 인덱스를 유지
            if match_idx is not None and match_idx > 0:
                return pairs[match_idx - 1][1]

        # 2순위: 타임스탬프 기반 - 마지막 날짜(오늘) 이전 날짜의 마지막 유효 close
        # 한국 지수이므로 KST(UTC+9) 기준으로 날짜를 판별
        def _kst_date(ts):
            return time.strftime("%Y-%m-%d", time.gmtime(ts + 9 * 60 * 60))

        last_date = _kst_date(pairs[-1][0])
        for i in range(len(pairs) - 1, -1, -1):
            ts, c = pairs[i]
            if _kst_date(ts) != last_date:
                return c

        # 3순위: 같은 날짜만 존재하면 마지막 유효 close
        return pairs[-1][1]
    except (IndexError, AttributeError, TypeError):
        return None


def fetch_vix():
    """
    VIX (CBOE Volatility Index)를 가져옵니다.
    Yahoo Finance에서 ^VIX 티커로 조회
    반환: { value, change, change_pct, previous_close }
    """
    return _fetch_yahoo_quote("^VIX", "VIX")


def fetch_market_indices():
    """
    주요 시장 지수를 가져옵니다. (4개 지수 병렬 조회)
    - S&P 500 (^GSPC)
    - NASDAQ Composite (^IXIC)
    - NASDAQ 100 (^NDX)
    - DOW Jones (^DJI)
    반환: { sp500: {...}, nasdaq: {...}, nasdaq100: {...}, dow: {...} }
    """
    indices = {
        "sp500": {"symbol": "^GSPC", "name": "S&P 500"},
        "nasdaq": {"symbol": "^IXIC", "name": "NASDAQ"},
        "nasdaq100": {"symbol": "^NDX", "name": "NASDAQ 100"},
        "dow": {"symbol": "^DJI", "name": "DOW"}
    }

    tasks = {}
    for key, info in indices.items():
        tasks[key] = lambda info=info: _fetch_yahoo_quote(info["symbol"], info["name"])

    quotes = _run_concurrently(tasks)

    result = {}
    for key, info in indices.items():
        quote = quotes.get(key)
        if quote:
            result[key] = {"name": info["name"], **quote}

    return result


def _fetch_daum_usd_krw():
    """
    Daum 금융 (finance.daum.net/exchanges) 내부 JSON API에서 USD/KRW 환율을 조회합니다.
    - 웹페이지가 사용하는 것과 동일한 경량 JSON API (HTML 스크래핑 불필요, JSON 1회 호출)
    - basePrice(매매기준율), changePrice(등락폭), changeRate(등락률)를 한 번에 제공
    반환: { value, change, change_pct, previous_close } 또는 None
    """
    url = "https://finance.daum.net/api/exchanges/summaries?page=1&perPage=20&locale=ko"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://finance.daum.net/exchanges",
        "apiKey": "daumfinance",
    }

    try:
        response = _make_request(url, headers=headers)
        if not response:
            return None

        data = json.loads(response)
        for item in data.get("data", []):
            if item.get("currencyCode") != "USD":
                continue

            value = item.get("basePrice")
            if value is None:
                return None

            change_price = item.get("changePrice") or 0.0   # 등락폭 (절대값)
            change_rate = item.get("changeRate") or 0.0     # 등락률 (부호 포함)

            # 등락폭은 부호 없는 절대값이므로 등락률의 부호를 적용
            change = change_price if change_rate > 0 else -change_price if change_rate < 0 else 0.0

            value = float(value)
            change = float(change)
            change_pct = float(change_rate) * 100

            return {
                "value": round(value, 2),
                "change": round(change, 2),
                "change_pct": round(change_pct, 2),
                "previous_close": round(value - change, 2),
            }
    except Exception as e:
        print(f"Error fetching USD/KRW from Daum: {e}")

    return None


def fetch_usd_krw():
    """
    USD/KRW 환율을 가져옵니다.
    1순위: Daum 금융 JSON API (finance.daum.net/exchanges와 동일 실시간 데이터)
    2순위: Yahoo Finance (KRW=X, Daum 조회 실패 시에만 폴백)
    반환: { value, change, change_pct, previous_close }
    """
    result = _fetch_daum_usd_krw()
    if result is not None:
        return result
    return _fetch_yahoo_quote("KRW=X", "USD/KRW", change_digits=4)


def fetch_us_treasury_10y():
    """
    미국 10년물 국채 수익률을 가져옵니다.
    Yahoo Finance에서 ^TNX 티커로 조회
    반환: { value, change, change_pct, previous_close }
    """
    return _fetch_yahoo_quote("^TNX", "US 10Y Treasury", digits=3, change_digits=3)


def fetch_us_dollar_index():
    """
    US Dollar Index (달러 인덱스, DXY)를 가져옵니다.
    Yahoo Finance에서 DX-Y.NYB 티커로 조회
    반환: { value, change, change_pct, previous_close }
    """
    return _fetch_yahoo_quote("DX-Y.NYB", "US Dollar Index")


def fetch_weekly_indices_data():
    """
    주요 지수의 주간 변동 데이터를 가져옵니다. (5개 지수 병렬 조회)
    - 미국: S&P 500 (^GSPC), NASDAQ (^IXIC), DOW (^DJI)
    - 한국: KOSPI (^KS11), KOSDAQ (^KQ11)

    지난주(월~금)의 시작 시가와 마지막 종가를 비교하여 주간 변동률을 계산합니다.

    반환: {
        sp500: {...}, nasdaq: {...}, dow: {...},
        kospi: {...}, kosdaq: {...},
        timestamp: "..."
    }
    """
    indices = {
        "sp500": {"symbol": "^GSPC", "name": "S&P 500"},
        "nasdaq": {"symbol": "^IXIC", "name": "NASDAQ"},
        "dow": {"symbol": "^DJI", "name": "DOW"},
        "kospi": {"symbol": "^KS11", "name": "KOSPI"},
        "kosdaq": {"symbol": "^KQ11", "name": "KOSDAQ"}
    }

    tasks = {}
    for key, info in indices.items():
        tasks[key] = lambda info=info: _fetch_weekly_change(info["symbol"])

    weekly_results = _run_concurrently(tasks)

    result = {}
    for key, info in indices.items():
        weekly = weekly_results.get(key)
        if weekly:
            result[key] = {
                "name": info["name"],
                **weekly
            }

    result["timestamp"] = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() + 9 * 60 * 60))

    return result


def _fetch_weekly_change(symbol):
    """
    지난주(월~금) 주간 변동률을 계산합니다.
    Yahoo Finance에서 range=1mo&interval=1d 데이터를 가져와
    지난주 월요일 시가와 금요일 종가를 비교합니다.

    한국 주식/지수: 월요일 시가 = 월요일 open, 금요일 종가 = 금요일 close
    미국 주식/지수: 한국 월요일 아침 = 미국 일요일 저녁이므로 실제 거래일이
                   한국 시간 기준으로 (월~금)이 아닌 (화~토)가 될 수 있습니다.
                   이 함수는 KST 기준 날짜로만 주를 구분하므로,
                   미국 지수는 한국 시각 월요일~금요일에 해당하는 거래일 데이터를 사용합니다.

    반환: {
        value: float,          # 지난주 마지막 거래일 종가
        week_change: float,    # 주간 변동 금액
        week_change_pct: float # 주간 변동률 (%)
    } 또는 None
    """
    encoded_symbol = urllib.parse.quote(symbol)
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded_symbol}?range=1mo&interval=1d&includePrePost=false"

    try:
        response = _make_request(url)
        if response:
            data = json.loads(response)
            result = data.get("chart", {}).get("result", [{}])[0]
            timestamps = result.get("timestamp", [])
            quote = result.get("indicators", {}).get("quote", [{}])[0]
            closes = quote.get("close", [])
            opens = quote.get("open", [])

            # 유효한 (timestamp, open, close) 페어만 추출
            pairs = []
            for i in range(len(timestamps)):
                if i < len(closes) and closes[i] is not None and i < len(opens) and opens[i] is not None:
                    pairs.append((timestamps[i], opens[i], closes[i]))

            if len(pairs) < 2:
                return None

            # KST(UTC+9) 기준 날짜 변환
            def _kst_date(ts):
                return time.strftime("%Y-%m-%d", time.gmtime(ts + 9 * 60 * 60))

            def _kst_weekday(ts):
                # 0=월요일 ... 6=일요일 (KST 기준)
                return time.gmtime(ts + 9 * 60 * 60).tm_wday

            # 지난주(월~금) 범위 계산 (KST 기준)
            now_kst = time.gmtime(time.time() + 9 * 60 * 60)
            today_weekday = now_kst.tm_wday

            # 이번 주 월요일 00:00 (KST)
            this_monday_ts = time.time() - (today_weekday * 86400)
            this_monday = time.gmtime(this_monday_ts + 9 * 60 * 60)
            this_monday_str = time.strftime("%Y-%m-%d", this_monday)

            # 지난주 월요일~금요일 날짜 문자열
            last_monday_ts = this_monday_ts - 7 * 86400
            last_friday_ts = this_monday_ts - 3 * 86400
            last_monday_str = time.strftime("%Y-%m-%d", time.gmtime(last_monday_ts + 9 * 60 * 60))
            last_friday_str = time.strftime("%Y-%m-%d", time.gmtime(last_friday_ts + 9 * 60 * 60))

            # 지난주 월요일 시가와 금요일 종가 찾기
            week_start_price = None
            week_end_price = None

            for i in range(len(pairs)):
                ts, o, c = pairs[i]
                date_str = _kst_date(ts)
                if date_str == last_monday_str:
                    week_start_price = o
                if date_str == last_friday_str:
                    week_end_price = c

            # 금요일 데이터가 없으면 (휴장 등) 가장 가까운 이전 거래일 사용
            if week_end_price is None:
                for i in range(len(pairs) - 1, -1, -1):
                    ts, o, c = pairs[i]
                    date_str = _kst_date(ts)
                    if date_str < last_friday_str and date_str >= last_monday_str:
                        week_end_price = c
                        break

            # 월요일 데이터가 없으면 (휴장 등) 가장 가까운 이후 거래일 사용
            if week_start_price is None:
                for i in range(len(pairs)):
                    ts, o, c = pairs[i]
                    date_str = _kst_date(ts)
                    if date_str > last_monday_str and date_str <= last_friday_str:
                        week_start_price = o
                        break

            if week_start_price is None or week_end_price is None or week_start_price <= 0:
                return None

            week_change = week_end_price - week_start_price
            week_change_pct = (week_change / week_start_price) * 100

            return {
                "value": round(week_end_price, 2),
                "week_change": round(week_change, 2),
                "week_change_pct": round(week_change_pct, 2)
            }
    except Exception as e:
        print(f"Error fetching weekly change for {symbol}: {e}")

    return None


def fetch_all_indices():
    """
    모든 시장 인덱스 데이터를 한 번에 가져옵니다. (각 항목 병렬 조회 + 60초 TTL 캐시)
    반환: {
        fear_greed: {...},
        vix: {...},
        indices: { sp500: {...}, nasdaq: {...}, dow: {...} },
        usd_krw: {...},
        treasury_10y: {...},
        us_dollar_index: {...},
        timestamp: "..."
    }
    """
    def _fetch():
        fetched = _run_concurrently({
            "fear_greed": fetch_fear_greed_index,
            "vix": fetch_vix,
            "indices": fetch_market_indices,
            "usd_krw": fetch_usd_krw,
            "treasury_10y": fetch_us_treasury_10y,
            "us_dollar_index": fetch_us_dollar_index,
        })

        return {
            "fear_greed": fetched.get("fear_greed"),
            "vix": fetched.get("vix"),
            "indices": fetched.get("indices") or {},
            "usd_krw": fetched.get("usd_krw"),
            "treasury_10y": fetched.get("treasury_10y"),
            "us_dollar_index": fetched.get("us_dollar_index"),
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() + 9 * 60 * 60))
        }

    return _cached("all_indices", _fetch)


def fetch_korea_market_indices():
    """
    한국 주요 시장 지수를 가져옵니다. (2개 지수 병렬 조회)
    - KOSPI (^KS11)
    - KOSDAQ (^KQ11)
    반환: { kospi: {...}, kosdaq: {...} }
    각 항목: { name, value, change, change_pct, previous_close }
    """
    indices = {
        "kospi": {"symbol": "^KS11", "name": "KOSPI"},
        "kosdaq": {"symbol": "^KQ11", "name": "KOSDAQ"}
    }

    tasks = {}
    for key, info in indices.items():
        tasks[key] = lambda info=info: _fetch_yahoo_quote(info["symbol"], info["name"])

    quotes = _run_concurrently(tasks)

    result = {}
    for key, info in indices.items():
        quote = quotes.get(key)
        if quote:
            result[key] = {"name": info["name"], **quote}

    return result


def _previous_korea_trading_date(trading_date):
    """주식 일정의 휴장 규칙과 등록된 임시휴장을 적용합니다."""
    day = datetime.strptime(trading_date, "%Y-%m-%d") - timedelta(days=1)
    overrides = market_events.load_overrides()
    holidays_by_year = {}
    for _ in range(30):
        if day.year not in holidays_by_year:
            holidays_by_year[day.year] = market_events.holidays_for_year("KR", day.year, overrides)
        if day.weekday() < 5 and day.date() not in holidays_by_year[day.year]:
            return day.date().isoformat()
        day -= timedelta(days=1)
    return None


def _fetch_naver_korea_index_daily_closes(code, trading_date):
    """일별 시세에서 당일과 직전 거래일의 종가를 날짜로 선택합니다."""
    response = _make_request(
        f"https://m.stock.naver.com/api/index/{code}/price?pageSize=10",
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://m.stock.naver.com/",
                 "Cache-Control": "no-cache"},
        retries=1, timeout=3,
    )
    if not response:
        return None
    rows = json.loads(response)
    if not isinstance(rows, list):
        return None
    daily = {row["localTradedAt"]: row["closePrice"] for row in rows}
    dates = (trading_date, _previous_korea_trading_date(trading_date))
    closes = tuple(float(str(daily[day]).replace(",", "")) for day in dates)
    if any(not math.isfinite(price) or price <= 0 for price in closes):
        return None
    return closes


def _fetch_naver_korea_index_close(code, trading_date):
    """장 종료 시세와 같은 날짜의 일별 종가가 일치할 때만 사용합니다."""
    url = f"https://polling.finance.naver.com/api/realtime/domestic/index/{code}"
    try:
        response = _make_request(url, headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://m.stock.naver.com/",
            "Cache-Control": "no-cache",
        }, retries=1, timeout=3)
        if not response:
            return None
        items = json.loads(response).get("datas", [])
        item = next((item for item in items if item.get("itemCode") == code), None)
        if not item or item.get("marketStatus") != "CLOSE":
            return None

        kst = timezone(timedelta(hours=9))
        quoted_at = datetime.fromisoformat(item["localTradedAt"])
        if quoted_at.tzinfo is None:
            return None
        quoted_at = quoted_at.astimezone(kst)
        if quoted_at.date().isoformat() != trading_date:
            return None
        # 수능일 등 연장된 정규장은 제공처의 종료 시각까지 기다립니다.
        session_end = datetime.strptime(item["stockExchangeType"]["endTime"], "%H%M")
        close_at = datetime.strptime(trading_date, "%Y-%m-%d").replace(
            hour=15, minute=30, tzinfo=kst
        )
        close_at = max(close_at, close_at.replace(hour=session_end.hour, minute=session_end.minute))
        if quoted_at < close_at:
            return None

        value = float(str(item["closePrice"]).replace(",", ""))
        change = float(str(item["compareToPreviousClosePrice"]).replace(",", ""))
        direction = str(item["compareToPreviousPrice"]["code"])
        if direction in ("1", "2"):
            change = abs(change)
        elif direction in ("4", "5"):
            change = -abs(change)
        elif direction != "3" or change != 0:
            return None
        previous_close = value - change
        if not all(math.isfinite(price) and price > 0 for price in (value, previous_close)):
            return None
        # CLOSE/시각이 먼저 갱신되어도 가격까지 확정되었다고 단정하지 않습니다.
        daily_closes = _fetch_naver_korea_index_daily_closes(code, trading_date)
        if (daily_closes is None
                or round(value, 2) != round(daily_closes[0], 2)
                or round(previous_close, 2) != round(daily_closes[1], 2)):
            return None
        value, previous_close = daily_closes
        change = value - previous_close
        return {
            "name": code, "value": round(value, 2),
            "previous_close": round(previous_close, 2),
            "change": round(change, 2),
            "change_pct": round(change / previous_close * 100, 2),
            "date": trading_date, "source": "Naver Finance closing index",
        }
    except (KeyError, AttributeError, TypeError, ValueError, OverflowError, OSError) as e:
        print(f"Error fetching Naver confirmed close for {code}: {e}")
    return None


def _fetch_korea_index_close(symbol, label, trading_date):
    """국내 실시간 종가를 우선 사용하고, 실패하면 Yahoo 확정 일봉으로 폴백합니다."""
    return (_fetch_naver_korea_index_close(label, trading_date)
            or _fetch_yahoo_korea_index_close(symbol, label, trading_date))


def _fetch_yahoo_korea_index_close(symbol, label, trading_date):
    """당일 정규장 종료까지 반영된 일봉만 종가로 사용합니다."""
    kst = timezone(timedelta(hours=9))
    close_at = datetime.strptime(trading_date, "%Y-%m-%d").replace(
        hour=15, minute=30, tzinfo=kst
    ).timestamp()
    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        f"{urllib.parse.quote(symbol)}?range=10d&interval=1d&includePrePost=false"
    )
    try:
        # 지연 시세/장애 때문에 다음 국내 종가 조회까지 오래 기다리지 않습니다.
        response = _make_request(url, retries=1, timeout=3)
        if not response:
            return None
        result = json.loads(response)["chart"]["result"][0]
        meta = result.get("meta", {})
        # 특별 거래일의 연장된 정규장도 종료 전에는 보내지 않습니다.
        session_end = meta.get("currentTradingPeriod", {}).get("regular", {}).get("end")
        if session_end and datetime.fromtimestamp(session_end, kst).date().isoformat() == trading_date:
            close_at = max(close_at, session_end)
        quote_time = meta.get("regularMarketTime")
        if (not quote_time or quote_time < close_at
                or datetime.fromtimestamp(quote_time, kst).date().isoformat() != trading_date):
            return None

        closes = result["indicators"]["quote"][0].get("close", [])
        daily = {}
        for ts, value in zip(result.get("timestamp", []), closes):
            if ts is not None:
                day = datetime.fromtimestamp(ts, kst).date().isoformat()
                daily[day] = value
        value = daily.get(trading_date)
        # 직전 거래일이 누락되면 더 오래된 종가로 등락률을 계산하지 않습니다.
        previous_close = daily.get(_previous_korea_trading_date(trading_date))
        if any(not isinstance(price, (int, float)) or not math.isfinite(price) or price <= 0
               for price in (value, previous_close)):
            return None
        # 메타데이터만 먼저 갱신되고 일봉이 아직 장중 값인 응답도 제외합니다.
        current = meta.get("regularMarketPrice")
        if not isinstance(current, (int, float)) or not math.isfinite(current) or abs(current - value) > 0.005:
            return None
        change = value - previous_close
        return {
            "name": label, "value": round(value, 2),
            "previous_close": round(previous_close, 2),
            "change": round(change, 2),
            "change_pct": round(change / previous_close * 100, 2),
            "date": trading_date, "source": "Yahoo Finance daily close",
        }
    except (KeyError, IndexError, TypeError, ValueError, OverflowError, OSError) as e:
        print(f"Error fetching confirmed close for {label}: {e}")
    return None


def fetch_korea_market_closing_indices(trading_date):
    """두 국내 지수의 종가를 조회하며 미확인 지수는 None으로 반환합니다."""
    return _run_concurrently({
        "kospi": lambda: _fetch_korea_index_close("^KS11", "KOSPI", trading_date),
        "kosdaq": lambda: _fetch_korea_index_close("^KQ11", "KOSDAQ", trading_date),
    })


def fetch_korea_market_close_data(trading_date=None):
    """
    한국장 마감 요약에 필요한 데이터를 한 번에 가져옵니다. (각 항목 병렬 조회 + 60초 TTL 캐시)
    - KOSPI, KOSDAQ (국내 지수)
    - USD/KRW 환율
    - (참고) 공포탐욕지수, VIX, 미국 주요 지수
    반환: {
        korea_indices: { kospi: {...}, kosdaq: {...} },
        usd_krw: {...},
        fear_greed: {...},
        vix: {...},
        us_indices: { sp500: {...}, nasdaq: {...}, dow: {...} },
        date: "...",
        timestamp: "..."
    }
    """
    now_kst = market_calendar.get_korea_now()
    trading_date = trading_date or now_kst.strftime("%Y-%m-%d")

    def _fetch():
        korea_indices = fetch_korea_market_closing_indices(trading_date)
        if not all(korea_indices.get(key) for key in ("kospi", "kosdaq")):
            return None  # 미확인 종가는 캐시하지 않고 다음 스케줄에서 재조회
        fetched = _run_concurrently({
            "usd_krw": fetch_usd_krw,
            "fear_greed": fetch_fear_greed_index,
            "vix": fetch_vix,
            "us_indices": fetch_market_indices,
        })

        return {
            "korea_indices": korea_indices,
            "usd_krw": fetched.get("usd_krw"),
            "fear_greed": fetched.get("fear_greed"),
            "vix": fetched.get("vix"),
            "us_indices": fetched.get("us_indices") or {},
            "date": trading_date,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() + 9 * 60 * 60))
        }

    return _cached(f"korea_market_close:{trading_date}", _fetch)


def _fetch_index_high_range(symbol, rng):
    """
    지정한 range의 일봉에서 유효 고가 중 최대값과 해당 날짜(UTC)를 반환합니다.
    반환: (high, "YYYY-MM-DD") — 조회 실패 시 (None, None)
    """
    encoded_symbol = urllib.parse.quote(symbol)
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded_symbol}"
        f"?range={rng}&interval=1d&includePrePost=false"
    )
    response = _make_request(url)
    if not response:
        return None, None

    try:
        data = json.loads(response)
        result = data.get("chart", {}).get("result", [{}])[0]
        timestamps = result.get("timestamp", [])
        quote = result.get("indicators", {}).get("quote", [{}])[0]
        highs = quote.get("high", [])

        # 유효한 고가 중 최대값과 해당 날짜 계산
        best_high = None
        best_ts = None
        for i in range(len(timestamps)):
            if i < len(highs) and highs[i] is not None:
                if best_high is None or highs[i] > best_high:
                    best_high = highs[i]
                    best_ts = timestamps[i]

        if best_high is not None and best_ts is not None:
            return best_high, time.strftime("%Y-%m-%d", time.gmtime(best_ts))
    except (IndexError, AttributeError, TypeError, json.JSONDecodeError):
        pass

    return None, None


def fetch_index_highs(symbol):
    """
    지수의 역대 최고가와 52주 최고가를 가져옵니다. (두 요청 병렬 조회)

    - 역대 최고가: range=max&interval=1d (전체 기간 일봉에서 최고가)
    - 52주 최고가: range=1y&interval=1d (1년치 일봉에서 최고가)

    반환: {
        all_time_high: float, all_time_high_date: "YYYY-MM-DD",
        week52_high: float, week52_high_date: "YYYY-MM-DD"
    } 또는 None
    """
    results = _run_concurrently({
        "max": lambda: _fetch_index_high_range(symbol, "max"),
        "week52": lambda: _fetch_index_high_range(symbol, "1y"),
    })

    all_time_high, all_time_high_date = results.get("max") or (None, None)
    week52_high, week52_high_date = results.get("week52") or (None, None)

    if all_time_high is None and week52_high is None:
        return None

    return {
        "all_time_high": all_time_high,
        "all_time_high_date": all_time_high_date,
        "week52_high": week52_high,
        "week52_high_date": week52_high_date
    }


def fetch_all_index_highs():
    """
    주요 지수들의 역대 최고가와 52주 최고가를 한 번에 가져옵니다. (병렬 조회)
    - 미국: S&P 500 (^GSPC), NASDAQ (^IXIC), DOW (^DJI)
    - 한국: KOSPI (^KS11), KOSDAQ (^KQ11)
    
    반환: {
        "sp500": {...}, "nasdaq": {...}, "dow": {...},
        "kospi": {...}, "kosdaq": {...}
    }
    """
    indices = {
        "sp500": {"symbol": "^GSPC", "name": "S&P 500"},
        "nasdaq": {"symbol": "^IXIC", "name": "NASDAQ"},
        "dow": {"symbol": "^DJI", "name": "DOW"},
        "kospi": {"symbol": "^KS11", "name": "KOSPI"},
        "kosdaq": {"symbol": "^KQ11", "name": "KOSDAQ"}
    }

    tasks = {}
    for key, info in indices.items():
        tasks[key] = lambda info=info: fetch_index_highs(info["symbol"])

    highs = _run_concurrently(tasks)

    result = {}
    for key, info in indices.items():
        item = highs.get(key)
        if item:
            result[key] = {
                "name": info["name"],
                "symbol": info["symbol"],
                **item
            }

    return result


def check_index_high_breakouts(data, current_prices):
    """
    지수들이 역대 최고가 또는 52주 최고가를 돌파했는지 감지합니다.
    
    data: fetch_all_index_highs() 결과
    current_prices: { key: current_price } 형태의 현재 지수 값
    
    반환: [(breakout_type, message), ...]
    - breakout_type: "ALL_TIME_HIGH" 또는 "WEEK52_HIGH"
    """
    alerts = []

    for key, info in data.items():
        name = info.get("name", key)
        current_price = current_prices.get(key)

        if current_price is None:
            continue

        all_time_high = info.get("all_time_high")
        all_time_high_date = info.get("all_time_high_date")
        week52_high = info.get("week52_high")
        week52_high_date = info.get("week52_high_date")

        # 역대 최고가 돌파 체크
        if all_time_high is not None and current_price > all_time_high:
            pct_above = ((current_price - all_time_high) / all_time_high) * 100
            alerts.append((
                "ALL_TIME_HIGH",
                f"🏆 <b>{name}</b> 역대 최고가 돌파! "
                f"현재 <b>{current_price:,.2f}</b> (기존 최고: {all_time_high:,.2f}, "
                f"{all_time_high_date or 'N/A'}) +{pct_above:.2f}%"
            ))
        # 52주 최고가 돌파 체크 (역대 최고가와 다를 때만)
        elif week52_high is not None and current_price > week52_high:
            pct_above = ((current_price - week52_high) / week52_high) * 100
            alerts.append((
                "WEEK52_HIGH",
                f"📈 <b>{name}</b> 52주 최고가 돌파! "
                f"현재 <b>{current_price:,.2f}</b> (기존 52주 최고: {week52_high:,.2f}, "
                f"{week52_high_date or 'N/A'}) +{pct_above:.2f}%"
            ))

    return alerts


def check_extreme_conditions(data):
    """
    극단적 시장 조건을 감지합니다.
    반환: [(condition_type, message), ...]
    """
    alerts = []
    
    # 1. VIX 극단값 체크
    if data.get("vix"):
        vix_value = data["vix"]["value"]
        vix_change = data["vix"].get("change_pct", 0)
        
        if vix_value >= 30:
            alerts.append(("VIX_HIGH", f"🚨 VIX {vix_value:.1f} - 시장 공포 극대화 (30 이상)"))
        elif vix_value >= 25:
            alerts.append(("VIX_ELEVATED", f"⚠️ VIX {vix_value:.1f} - 변동성 확대 (25 이상)"))
        
        if vix_change >= 10:
            alerts.append(("VIX_SPIKE", f"📈 VIX 급등 +{vix_change:.1f}% - 시장 충격"))
        elif vix_change <= -10:
            alerts.append(("VIX_DROP", f"📉 VIX 급락 {vix_change:.1f}% - 시장 안정"))
    
    # 2. 공포탐욕지수 극단값 체크
    if data.get("fear_greed"):
        fg_value = data["fear_greed"]["value"]
        
        if fg_value is not None:
            if fg_value <= 15:
                alerts.append(("FG_EXTREME_FEAR", f"🔴 공포탐욕지수 {fg_value:.0f} - 극도 공포 (15 이하)"))
            elif fg_value <= 25:
                alerts.append(("FG_FEAR", f"🟠 공포탐욕지수 {fg_value:.0f} - 공포 (25 이하)"))
            elif fg_value >= 85:
                alerts.append(("FG_EXTREME_GREED", f"🟢 공포탐욕지수 {fg_value:.0f} - 극도 탐욕 (85 이상)"))
            elif fg_value >= 75:
                alerts.append(("FG_GREED", f"🟡 공포탐욕지수 {fg_value:.0f} - 탐욕 (75 이상)"))
    
    # 3. 지수 급등락 체크
    if data.get("indices"):
        for key, idx_data in data["indices"].items():
            change_pct = idx_data.get("change_pct", 0)
            name = idx_data.get("name", key)
            
            if change_pct <= -2:
                alerts.append((f"INDEX_DROP_{key}", f"📉 {name} {change_pct:.2f}% 하락"))
            elif change_pct >= 2:
                alerts.append((f"INDEX_RISE_{key}", f"📈 {name} +{change_pct:.2f}% 상승"))
    
    # 4. 환율 급변동 체크
    if data.get("usd_krw"):
        krw_change = data["usd_krw"].get("change_pct", 0)
        krw_value = data["usd_krw"].get("value", 0)
        
        if abs(krw_change) >= 2:
            direction = "상승" if krw_change > 0 else "하락"
            alerts.append(("KRW_SPIKE", f"💱 원/달러 {krw_value:.0f}원 ({direction} {abs(krw_change):.2f}%)"))
    
    # 5. 국채수익률 급변동 체크
    if data.get("treasury_10y"):
        tnx_change = data["treasury_10y"].get("change", 0)
        tnx_value = data["treasury_10y"].get("value", 0)
        
        if abs(tnx_change) >= 0.1:
            direction = "상승" if tnx_change > 0 else "하락"
            alerts.append(("TNX_MOVE", f"📊 미국 10년물 국채 {tnx_value:.3f}% ({direction} {abs(tnx_change):.3f}%p)"))
    
    # 6. 달러 인덱스 급변동 체크
    if data.get("us_dollar_index"):
        dxy_change = data["us_dollar_index"].get("change_pct", 0)
        dxy_value = data["us_dollar_index"].get("value", 0)
        
        if abs(dxy_change) >= 1:
            direction = "상승" if dxy_change > 0 else "하락"
            alerts.append(("DXY_MOVE", f"💵 달러 인덱스 {dxy_value:.2f} ({direction} {abs(dxy_change):.2f}%)"))
    
    return alerts


def format_indices_report(data):
    """
    인덱스 데이터를 보기 좋은 리포트 형식으로 변환합니다.
    """
    lines = []
    lines.append("<b>📊 시장 인덱스 현황</b>")
    lines.append(f"⏱ 조회시간: <code>{data.get('timestamp', '')}</code>")
    lines.append("━━━━━━━━━━━━━━━━━━━")
    
    # Fear & Greed Index
    fg = data.get("fear_greed")
    if fg and fg.get("value") is not None:
        value = fg["value"]
        classification = fg.get("classification", "")
        
        # 이모지 선택
        if value <= 25:
            emoji = "🔴"
        elif value <= 45:
            emoji = "🟠"
        elif value <= 55:
            emoji = "🟡"
        elif value <= 75:
            emoji = "🟢"
        else:
            emoji = "🟢🔥"
        
        # 이전 대비 변화
        prev = fg.get("previous_close", 0)
        change = value - prev if prev else 0
        change_str = f" (전일 대비 {change:+.1f})" if prev else ""
        
        lines.append(f"\n<b>🎭 공포탐욕지수</b>")
        lines.append(f"{emoji} <b>{value:.1f}</b> - {classification}{change_str}")
        
        # 추이
        week_ago = fg.get("week_ago", 0)
        month_ago = fg.get("month_ago", 0)
        if week_ago:
            week_change = value - week_ago
            lines.append(f"• 1주 전: {week_ago:.1f} ({week_change:+.1f})")
        if month_ago:
            month_change = value - month_ago
            lines.append(f"• 1달 전: {month_ago:.1f} ({month_change:+.1f})")
    
    # VIX
    vix = data.get("vix")
    if vix:
        value = vix["value"]
        change_pct = vix.get("change_pct", 0)
        
        # 이모지 선택
        if value >= 30:
            emoji = "🔴"
        elif value >= 20:
            emoji = "🟠"
        else:
            emoji = "🟢"
        
        change_emoji = "📈" if change_pct > 0 else "📉" if change_pct < 0 else "➡️"
        
        lines.append(f"\n<b>📊 VIX (변동성 지수)</b>")
        lines.append(f"{emoji} <b>{value:.2f}</b> {change_emoji} {change_pct:+.2f}%")
        
        if value >= 25:
            lines.append(f"<i>⚠️ 변동성 확대 구간</i>")
        elif value >= 20:
            lines.append(f"<i>주의 필요</i>")
    
    # Market Indices
    indices = data.get("indices", {})
    if indices:
        lines.append(f"\n<b>📈 주요 지수</b>")
        for key in ["sp500", "nasdaq", "nasdaq100", "dow"]:
            idx = indices.get(key)
            if idx:
                name = idx.get("name", key)
                value = idx.get("value", 0)
                change_pct = idx.get("change_pct", 0)
                
                emoji = "🟢" if change_pct > 0 else "🔴" if change_pct < 0 else "⚪"
                sign = "+" if change_pct > 0 else ""
                
                lines.append(f"• {emoji} <b>{name}</b>: {value:,.2f} ({sign}{change_pct:.2f}%)")
    
    # USD/KRW
    krw = data.get("usd_krw")
    if krw:
        value = krw["value"]
        change_pct = krw.get("change_pct", 0)
        
        emoji = "📈" if change_pct > 0 else "📉" if change_pct < 0 else "➡️"
        
        lines.append(f"\n<b>💱 USD/KRW 환율</b>")
        lines.append(f"{emoji} <b>{value:,.0f}원</b> ({change_pct:+.2f}%)")
    
    # US Treasury 10Y
    tnx = data.get("treasury_10y")
    if tnx:
        value = tnx["value"]
        change = tnx.get("change", 0)
        
        emoji = "📈" if change > 0 else "📉" if change < 0 else "➡️"
        
        lines.append(f"\n<b>🏛️ 미국 10년물 국채</b>")
        lines.append(f"{emoji} <b>{value:.3f}%</b> ({change:+.3f}%p)")
    
    # US Dollar Index (DXY)
    dxy = data.get("us_dollar_index")
    if dxy:
        value = dxy["value"]
        change_pct = dxy.get("change_pct", 0)
        
        emoji = "📈" if change_pct > 0 else "📉" if change_pct < 0 else "➡️"
        
        lines.append(f"\n<b>💵 달러 인덱스 (DXY)</b>")
        lines.append(f"{emoji} <b>{value:.2f}</b> ({change_pct:+.2f}%)")
    
    lines.append("\n━━━━━━━━━━━━━━━━━━━")
    
    return "\n".join(lines)


def format_us_market_close_report(data):
    """
    미국장 마감 후 (미국 동부 기준 16:00 이후) 시장 요약 리포트 형식
    """
    lines = []
    lines.append("<b>🇺🇸 미국장 마감 요약</b>")
    lines.append(f"⏱ 조회시간: <code>{data.get('timestamp', '')}</code>")
    lines.append("━━━━━━━━━━━━━━━━━━━")
    
    # Fear & Greed Index
    fg = data.get("fear_greed")
    if fg and fg.get("value") is not None:
        value = fg["value"]
        classification = fg.get("classification", "")
        
        if value <= 25:
            emoji = "🔴"
        elif value <= 45:
            emoji = "🟠"
        elif value <= 55:
            emoji = "🟡"
        elif value <= 75:
            emoji = "🟢"
        else:
            emoji = "🟢🔥"
        
        lines.append(f"\n<b>🎭 공포탐욕지수: {emoji} {value:.1f} ({classification})</b>")
    
    # VIX
    vix = data.get("vix")
    if vix:
        value = vix["value"]
        change_pct = vix.get("change_pct", 0)
        
        if value >= 25:
            emoji = "⚠️"
        else:
            emoji = "✅"
        
        lines.append(f"<b>📊 VIX:</b> {value:.2f} ({change_pct:+.2f}%) {emoji}")
    
    # Market Indices
    indices = data.get("indices", {})
    if indices:
        lines.append("\n<b>📈 주요 지수</b>")
        for key in ["sp500", "nasdaq", "nasdaq100", "dow"]:
            idx = indices.get(key)
            if idx:
                name = idx.get("name", key)
                value = idx.get("value", 0)
                change_pct = idx.get("change_pct", 0)
                
                emoji = "🟢" if change_pct > 0 else "🔴" if change_pct < 0 else "⚪"
                sign = "+" if change_pct > 0 else ""
                
                lines.append(f"• {emoji} {name}: {value:,.2f} ({sign}{change_pct:.2f}%)")
    
    # USD/KRW
    krw = data.get("usd_krw")
    if krw:
        value = krw["value"]
        change_pct = krw.get("change_pct", 0)
        lines.append(f"\n<b>💱 USD/KRW:</b> {value:,.0f}원 ({change_pct:+.2f}%)")
    
    # US Treasury 10Y
    tnx = data.get("treasury_10y")
    if tnx:
        value = tnx["value"]
        change = tnx.get("change", 0)
        lines.append(f"<b>🏛️ 미국 10년물:</b> {value:.3f}% ({change:+.3f}%p)")
    
    # US Dollar Index (DXY)
    dxy = data.get("us_dollar_index")
    if dxy:
        value = dxy["value"]
        change_pct = dxy.get("change_pct", 0)
        lines.append(f"<b>💵 달러 인덱스:</b> {value:.2f} ({change_pct:+.2f}%)")
    
    # 극단 조건 경고
    extreme_alerts = check_extreme_conditions(data)
    if extreme_alerts:
        lines.append("\n━━━━━━━━━━━━━━━━━━━")
        lines.append("<b>⚠️ 극단 조건 경고</b>")
        for alert_type, message in extreme_alerts:
            lines.append(f"• {message}")

    
    return "\n".join(lines)




def format_korea_market_close_report(data):
    """
    한국장 마감 후 (한국 시간 15:30 이후) 시장 요약 리포트 형식
    - KOSPI, KOSDAQ (국내 지수)
    - USD/KRW 환율
    - (참고) 공포탐욕지수, VIX, 미국 주요 지수
    """
    if not data:
        return "🇰🇷 당일 한국장 종가가 아직 확인되지 않았습니다. 잠시 후 다시 조회해 주세요."
    lines = []
    lines.append("<b>🇰🇷 한국장 마감 요약</b>")
    lines.append(f"📅 <code>{data.get('date', '')}</code> · ⏱ <code>{data.get('timestamp', '')}</code>")
    lines.append("━━━━━━━━━━━━━━━━━━━")

    # 국내 주요 지수 (KOSPI, KOSDAQ)
    korea_indices = data.get("korea_indices", {})
    if korea_indices:
        lines.append("\n<b>📈 국내 주요 지수 (정규장 종가)</b>")
        for key in ["kospi", "kosdaq"]:
            idx = korea_indices.get(key)
            if idx:
                name = idx.get("name", key)
                value = idx.get("value", 0)
                change = idx.get("change", 0)
                change_pct = idx.get("change_pct", 0)

                emoji = "🟢" if change_pct > 0 else "🔴" if change_pct < 0 else "⚪"
                sign = "+" if change_pct > 0 else ""

                lines.append(f"• {emoji} <b>{name}</b>: {value:,.2f} ({sign}{change_pct:.2f}% · {sign}{change:,.2f})")

    # USD/KRW 환율
    krw = data.get("usd_krw")
    if krw:
        value = krw["value"]
        change = krw.get("change", 0)
        change_pct = krw.get("change_pct", 0)

        emoji = "📈" if change_pct > 0 else "📉" if change_pct < 0 else "➡️"

        lines.append("\n<b>💱 USD/KRW 환율 (조회 시점 참고값)</b>")
        lines.append(f"{emoji} <b>{value:,.0f}원</b> ({change_pct:+.2f}% · {change:+,.0f}원)")

    # 참고 정보
    lines.append("\n━━━━━━━━━━━━━━━━━━━")
    lines.append("<b>🌐 참고 정보</b>")

    fg = data.get("fear_greed")
    if fg and fg.get("value") is not None:
        value = fg["value"]
        classification = fg.get("classification", "")
        lines.append(f"• 🎭 공포탐욕지수: <b>{value:.1f}</b> ({classification})")

    vix = data.get("vix")
    if vix:
        value = vix["value"]
        change_pct = vix.get("change_pct", 0)
        lines.append(f"• 📊 VIX: {value:.2f} ({change_pct:+.2f}%)")

    us_indices = data.get("us_indices", {})
    if us_indices:
        for key in ["sp500", "nasdaq", "dow"]:
            idx = us_indices.get(key)
            if idx:
                name = idx.get("name", key)
                value = idx.get("value", 0)
                change_pct = idx.get("change_pct", 0)

                emoji = "🟢" if change_pct > 0 else "🔴" if change_pct < 0 else "⚪"
                sign = "+" if change_pct > 0 else ""

                lines.append(f"• {emoji} {name}: {value:,.2f} ({sign}{change_pct:.2f}%)")


    return "\n".join(lines)
