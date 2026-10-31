import urllib.request
import urllib.parse
import json
import ssl
import time
import math
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import market_calendar
import toss_api


_nasdaq_close_cache = {}


def _previous_us_trading_date(session_date):
    """Return the trading day preceding the current US market date."""
    day = datetime.fromisoformat(session_date) - timedelta(days=1)
    for _ in range(14):
        if market_calendar.is_us_trading_day(day):
            return day.strftime("%Y-%m-%d")
        day -= timedelta(days=1)
    return None


def _get_nasdaq_previous_close(ticker, instrument_type, trade_date):
    """Read an exact dated US stock/ETF close from Nasdaq."""
    if not trade_date:
        return None
    assetclasses = {"EQUITY": ("stocks",), "ETF": ("etf",)}.get(
        instrument_type, ("stocks", "etf")
    )
    cache_key = (ticker, trade_date)
    if cache_key in _nasdaq_close_cache:
        return _nasdaq_close_cache[cache_key]

    next_date = (datetime.fromisoformat(trade_date) + timedelta(days=1)).strftime("%Y-%m-%d")
    for assetclass in assetclasses:
        params = urllib.parse.urlencode({
            "assetclass": assetclass, "fromdate": trade_date,
            "todate": next_date, "limit": 5,
        })
        url = f"https://api.nasdaq.com/api/quote/{urllib.parse.quote(ticker)}/historical?{params}"
        try:
            data = _make_request(url, retries=1)
            if data.get("status", {}).get("rCode") != 200:
                continue
            rows = data.get("data", {}).get("tradesTable", {}).get("rows", [])
            for row in rows:
                row_date = datetime.strptime(row["date"], "%m/%d/%Y").strftime("%Y-%m-%d")
                if row_date == trade_date:
                    close = float(str(row["close"]).replace("$", "").replace(",", ""))
                    if math.isfinite(close) and close > 0:
                        _nasdaq_close_cache[cache_key] = close
                        return close
        except (AttributeError, KeyError, TypeError, ValueError, OSError):
            continue
    return None


def _get_us_previous_close(ticker, instrument_type, session_date, daily_bars):
    """Use Nasdaq, or another provider's close only for the same prior date."""
    trade_date = _previous_us_trading_date(session_date)
    if not trade_date:
        return None
    close = _get_nasdaq_previous_close(ticker, instrument_type, trade_date)
    if close is not None:
        return close
    for ts, fallback_close in reversed(list(daily_bars)):
        if (toss_api._market_local_date_str(ts, ticker) == trade_date
                and isinstance(fallback_close, (int, float))
                and math.isfinite(fallback_close) and fallback_close > 0):
            return fallback_close
    return None


def fetch_price_alert_snapshot(ticker):
    """Fetch today's full minute range, including pre/post, independently of analysis.

    Use actual dated daily closes for the baseline: chartPreviousClose can refer
    to the beginning of a multi-day chart rather than the previous trading day.
    Yahoo is used even when Toss is configured, to recover between-poll extremes.
    """
    def positive(value):
        return isinstance(value, (int, float)) and math.isfinite(value) and value > 0

    session_date = toss_api._market_local_date_str(time.time(), ticker)
    base = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(ticker)}"
    daily = _make_request(base + "?range=5d&interval=1d&includePrePost=false")
    intraday = _make_request(base + "?range=1d&interval=1m&includePrePost=true")
    try:
        day = daily["chart"]["result"][0]
        minute = intraday["chart"]["result"][0]
        daily_closes = day["indicators"]["quote"][0]["close"]
        daily_bars = list(zip(day["timestamp"], daily_closes))
        instrument_type = day.get("meta", {}).get("instrumentType")
        if not toss_api._is_korean_ticker(ticker) and instrument_type in ("EQUITY", "ETF"):
            previous_close = _get_us_previous_close(ticker, instrument_type, session_date, daily_bars)
        else:
            prior = [(ts, close) for ts, close in daily_bars
                     if toss_api._market_local_date_str(ts, ticker) < session_date and positive(close)]
            previous_close = max(prior)[1] if prior else None
        if previous_close is None:
            return None
        quote = minute["indicators"]["quote"][0]
        bars = [(ts, close, high, low) for ts, close, high, low in zip(
            minute["timestamp"], quote["close"], quote["high"], quote["low"])
            if toss_api._market_local_date_str(ts, ticker) == session_date
            and all(positive(v) for v in (close, high, low)) and low <= close <= high]
        if not bars:
            return None  # Never relabel yesterday's quote as a new session.
        latest = max(bars, key=lambda bar: bar[0])
        meta = minute.get("meta", {})
        return {"current_price": latest[1], "previous_close": previous_close,
                "session_high": max(bar[2] for bar in bars),
                "session_low": min(bar[3] for bar in bars),
                "session_date": session_date, "quote_timestamp": latest[0],
                "currency": meta.get("currency", "USD"),
                "name": meta.get("longName") or meta.get("shortName") or ticker}
    except (KeyError, IndexError, TypeError, AttributeError):
        return None

def _make_request(url, retries=3, delay=2):
    """HTTP request helper with retry logic."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
    }
    
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, context=ctx, timeout=10) as response:
                if response.status == 200:
                    return json.loads(response.read().decode("utf-8"))
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(delay)
                continue
            raise e
    return None


def is_toss_enabled():
    """Check if Toss Open API is configured."""
    return toss_api.is_configured()


def fetch_current_prices_batch(tickers):
    """
    Fetch current prices for multiple tickers.
    Uses Toss API (fast, batch) if configured, otherwise falls back to individual Yahoo Finance requests.
    토스에서 일부 종목만 반환되면 누락된 종목만 개별 조회로 보완합니다.
    Returns: { ticker: {price, previous_close, currency} }
    """
    tickers = list(dict.fromkeys(
        t.strip().upper() for t in (tickers or []) if t and t.strip()
    ))
    if not tickers:
        return {}

    result = {}
    missing = list(tickers)
    toss_batch_broken = False

    # 1순위: 토스증권 Open API (배치 요청 1회)
    if toss_api.is_configured():
        toss_symbols = {ticker: toss_api.to_toss_symbol(ticker) for ticker in tickers}
        try:
            prices = toss_api.fetch_current_prices(list(toss_symbols.values()))
        except Exception:
            prices = None
        if prices:
            for ticker, sym in toss_symbols.items():
                info = prices.get(sym)
                if info and info.get("price") is not None:
                    result[ticker] = {
                        "price": info["price"],
                        "previous_close": None,  # Calculated from candles in fetch_stock_data
                        "currency": info.get("currency"),
                    }
            # 배치에서 누락된 종목(미지원 심볼 등)만 개별 조회 대상으로
            missing = [t for t in tickers if t not in result]
        else:
            # 배치 요청 자체가 실패/빈 응답인 경우 → 심볼별 토스 재시도는 중복 실패 호출이므로 생략
            toss_batch_broken = True

    # 2순위: 누락 종목만 개별 조회 (폴백, 병렬 처리)
    # 배치가 부분 성공한 경우 누락 종목은 토스 개별 재시도 후 Yahoo로 폴백
    if missing:
        def _fetch_missing(ticker):
            try:
                return ticker, fetch_current_price_only(ticker, allow_toss=not toss_batch_broken)
            except Exception:
                return ticker, None

        workers = min(8, len(missing))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            # executor.map은 입력 순서를 유지하므로 결과 dict의 순서도 유지됨
            for ticker, data in executor.map(_fetch_missing, missing):
                if data:
                    result[ticker] = data
    return result


def _get_realtime_price(ticker):
    """
    Get latest valid price from 1m/5m candles (includes pre/post market).
    Falls back to previous close if no data available.
    Returns: (current_price, previous_close, currency, market_state)
    """
    encoded_ticker = urllib.parse.quote(ticker)

    current_price = None
    previous_close = None
    currency = None
    market_state = None

    # Try 1m candles first (2d range)
    url_1m = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded_ticker}"
        f"?range=2d&interval=1m&includePrePost=true"
    )
    data_1m = _make_request(url_1m)

    if data_1m:
        try:
            result = data_1m.get("chart", {}).get("result", [{}])[0]
            meta = result.get("meta", {})

            currency = meta.get("currency", "USD")
            market_state = meta.get("marketState", "UNKNOWN")

            previous_close = (meta.get("previousClose") or
                            meta.get("regularMarketPreviousClose") or
                            meta.get("chartPreviousClose"))

            # Use last valid close from 1m candles
            closes_1m = result.get("indicators", {}).get("quote", [{}])[0].get("close", [])
            for i in range(len(closes_1m) - 1, -1, -1):
                if closes_1m[i] is not None:
                    current_price = closes_1m[i]
                    break

            if current_price is None:
                current_price = meta.get("regularMarketPrice")
        except (IndexError, AttributeError, TypeError):
            pass

    # Fallback to 5m candles if 1m failed
    if current_price is None:
        url_5m = (
            f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded_ticker}"
            f"?range=5d&interval=5m&includePrePost=true"
        )
        data_5m = _make_request(url_5m)

        if data_5m:
            try:
                result = data_5m.get("chart", {}).get("result", [{}])[0]
                meta = result.get("meta", {})

                if currency is None:
                    currency = meta.get("currency", "USD")
                if market_state is None:
                    market_state = meta.get("marketState", "UNKNOWN")
                if previous_close is None:
                    previous_close = (meta.get("previousClose") or
                                    meta.get("regularMarketPreviousClose") or
                                    meta.get("chartPreviousClose"))

                closes_5m = result.get("indicators", {}).get("quote", [{}])[0].get("close", [])
                for i in range(len(closes_5m) - 1, -1, -1):
                    if closes_5m[i] is not None:
                        current_price = closes_5m[i]
                        break

                if current_price is None:
                    current_price = meta.get("regularMarketPrice")
            except (IndexError, AttributeError, TypeError):
                pass

    return current_price, previous_close, currency, market_state


def _get_daily_data(ticker):
    """
    Fetch daily OHLCV data (1y range) for technical indicators.
    Returns: { closes, highs, lows, opens, volumes, timestamps, currency, previous_close }
    """
    encoded_ticker = urllib.parse.quote(ticker)
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded_ticker}?range=1y&interval=1d&includePrePost=false"
    
    data = _make_request(url)
    if not data:
        return None
    
    try:
        result = data.get("chart", {}).get("result", [{}])[0]
        meta = result.get("meta", {})
        currency = meta.get("currency", "USD")
        
        timestamps = result.get("timestamp", [])
        quote = result.get("indicators", {}).get("quote", [{}])[0]
        
        closes = quote.get("close", [])
        highs = quote.get("high", [])
        lows = quote.get("low", [])
        opens = quote.get("open", [])
        volumes = quote.get("volume", [])
        
        if not timestamps or not closes:
            return None
        
        # Data cleansing
        cleaned = {
            "timestamps": [],
            "closes": [],
            "highs": [],
            "lows": [],
            "opens": [],
            "volumes": []
        }
        
        for i in range(len(timestamps)):
            if (i < len(closes) and closes[i] is not None and
                i < len(highs) and highs[i] is not None and
                i < len(lows) and lows[i] is not None and
                i < len(opens) and opens[i] is not None):
                
                cleaned["timestamps"].append(timestamps[i])
                cleaned["closes"].append(closes[i])
                cleaned["highs"].append(highs[i])
                cleaned["lows"].append(lows[i])
                cleaned["opens"].append(opens[i])
                cleaned["volumes"].append(
                    volumes[i] if (i < len(volumes) and volumes[i] is not None) else 0
                )
        
        if len(cleaned["closes"]) < 20:
            return None
        
        # 일봉의 거래일을 시장 현지 날짜로 판별합니다. currentTradingPeriod.regular.start는
        # 장 마감 후 다음 세션으로 넘어갈 수 있어 오늘 일봉과 비교할 기준으로 부적합합니다.
        cleaned["currency"] = currency
        cleaned["name"] = meta.get("longName") or meta.get("shortName")
        cleaned["market_state"] = meta.get("marketState", "UNKNOWN")

        instrument_type = meta.get("instrumentType")
        if not toss_api._is_korean_ticker(ticker) and instrument_type in ("EQUITY", "ETF"):
            session_date = toss_api._market_local_date_str(time.time(), ticker)
            prev_close = _get_us_previous_close(
                ticker, instrument_type, session_date,
                zip(cleaned["timestamps"], cleaned["closes"]),
            )
        else:
            prev_close = toss_api._compute_previous_close(cleaned, ticker)

        if (prev_close is None or prev_close <= 0) and instrument_type not in ("EQUITY", "ETF"):
            # 마지막 캔들 종가가 없으면 meta 값 사용 (최종 fallback)
            prev_close = (meta.get("previousClose") or
                         meta.get("regularMarketPreviousClose") or
                         meta.get("chartPreviousClose"))

        cleaned["previous_close"] = prev_close
        return cleaned
        
    except (IndexError, AttributeError, TypeError):
        return None


def _get_intraday_data(ticker, interval="5m", range_str="5d"):
    """
    분/시간봉 데이터를 가져옵니다 (기술적 지표 계산용).
    
    interval: 5m, 15m, 30m, 60m, 1h
    range: 1d, 5d, 1mo, 3mo (interval에 따라 적절히 선택)
    
    반환: { closes, highs, lows, opens, volumes, timestamps, currency }
    """
    encoded_ticker = urllib.parse.quote(ticker)
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded_ticker}?range={range_str}&interval={interval}&includePrePost=true"
    
    data = _make_request(url)
    if not data:
        return None
    
    try:
        result = data.get("chart", {}).get("result", [{}])[0]
        meta = result.get("meta", {})
        currency = meta.get("currency", "USD")
        
        timestamps = result.get("timestamp", [])
        quote = result.get("indicators", {}).get("quote", [{}])[0]
        
        closes = quote.get("close", [])
        highs = quote.get("high", [])
        lows = quote.get("low", [])
        opens = quote.get("open", [])
        volumes = quote.get("volume", [])
        
        if not timestamps or not closes:
            return None
        
        # Data cleansing
        cleaned = {
            "timestamps": [],
            "closes": [],
            "highs": [],
            "lows": [],
            "opens": [],
            "volumes": []
        }
        
        for i in range(len(timestamps)):
            if (i < len(closes) and closes[i] is not None and
                i < len(highs) and highs[i] is not None and
                i < len(lows) and lows[i] is not None and
                i < len(opens) and opens[i] is not None):
                
                cleaned["timestamps"].append(timestamps[i])
                cleaned["closes"].append(closes[i])
                cleaned["highs"].append(highs[i])
                cleaned["lows"].append(lows[i])
                cleaned["opens"].append(opens[i])
                cleaned["volumes"].append(
                    volumes[i] if (i < len(volumes) and volumes[i] is not None) else 0
                )
        
        # 지표 계산에 필요한 최소 데이터 포인트 (MACD에 slow(26) + signal(9) = 35 필요)
        if len(cleaned["closes"]) < 40:
            return None
        
        cleaned["currency"] = currency
        return cleaned
        
    except (IndexError, AttributeError, TypeError):
        return None


def _get_weekly_data(ticker):
    """
    주봉 데이터를 가져옵니다 (기술적 지표 계산용).
    range=2y, interval=1wk → 약 104개의 주봉 데이터
    반환: { closes, highs, lows, opens, volumes, timestamps, currency }
    """
    encoded_ticker = urllib.parse.quote(ticker)
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded_ticker}?range=2y&interval=1wk&includePrePost=true"
    
    data = _make_request(url)
    if not data:
        return None
    
    try:
        result = data.get("chart", {}).get("result", [{}])[0]
        meta = result.get("meta", {})
        currency = meta.get("currency", "USD")
        
        timestamps = result.get("timestamp", [])
        quote = result.get("indicators", {}).get("quote", [{}])[0]
        
        closes = quote.get("close", [])
        highs = quote.get("high", [])
        lows = quote.get("low", [])
        opens = quote.get("open", [])
        volumes = quote.get("volume", [])
        
        if not timestamps or not closes:
            return None
        
        # Data cleansing
        cleaned = {
            "timestamps": [],
            "closes": [],
            "highs": [],
            "lows": [],
            "opens": [],
            "volumes": []
        }
        
        for i in range(len(timestamps)):
            if (i < len(closes) and closes[i] is not None and
                i < len(highs) and highs[i] is not None and
                i < len(lows) and lows[i] is not None and
                i < len(opens) and opens[i] is not None):
                
                cleaned["timestamps"].append(timestamps[i])
                cleaned["closes"].append(closes[i])
                cleaned["highs"].append(highs[i])
                cleaned["lows"].append(lows[i])
                cleaned["opens"].append(opens[i])
                cleaned["volumes"].append(
                    volumes[i] if (i < len(volumes) and volumes[i] is not None) else 0
                )
        
        if len(cleaned["closes"]) < 40:
            return None
        
        cleaned["currency"] = currency
        return cleaned
        
    except (IndexError, AttributeError, TypeError):
        return None


def fetch_highs_data(ticker):
    """
    종목의 역대 최고가와 52주 최고가를 가져옵니다.
    
    - 역대 최고가: range=max&interval=1d (전체 기간 일봉에서 최고가)
    - 52주 최고가: range=1y&interval=1d (1년치 일봉에서 최고가)
    - fallback: meta.fiftyTwoWeekHigh 사용
    
    반환: {
        all_time_high: float, all_time_high_date: "YYYY-MM-DD",
        week52_high: float, week52_high_date: "YYYY-MM-DD",
        currency: str
    } 또는 None
    """
    ticker = ticker.strip().upper()
    encoded_ticker = urllib.parse.quote(ticker)

    all_time_high = None
    all_time_high_date = None
    week52_high = None
    week52_high_date = None
    currency = None

    # ================================================================
    # 1. 역대 최고가: range=max&interval=1d (전체 기간 일봉)
    # ================================================================
    url_max = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded_ticker}"
        f"?range=max&interval=1d&includePrePost=false"
    )
    data_max = _make_request(url_max)

    if data_max:
        try:
            result = data_max.get("chart", {}).get("result", [{}])[0]
            meta = result.get("meta", {})
            currency = meta.get("currency", "USD")

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
                all_time_high = best_high
                all_time_high_date = time.strftime("%Y-%m-%d", time.gmtime(best_ts))
        except (IndexError, AttributeError, TypeError):
            pass

    # ================================================================
    # 2. 52주 최고가: range=1y&interval=1d (1년치 일봉)
    # ================================================================
    url_1y = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded_ticker}"
        f"?range=1y&interval=1d&includePrePost=false"
    )
    data_1y = _make_request(url_1y)

    if data_1y:
        try:
            result = data_1y.get("chart", {}).get("result", [{}])[0]
            meta = result.get("meta", {})

            if currency is None:
                currency = meta.get("currency", "USD")

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
                week52_high = best_high
                week52_high_date = time.strftime("%Y-%m-%d", time.gmtime(best_ts))
            else:
                # fallback: meta.fiftyTwoWeekHigh
                week52_high = meta.get("fiftyTwoWeekHigh")
        except (IndexError, AttributeError, TypeError):
            pass

    if all_time_high is None and week52_high is None:
        return None

    return {
        "all_time_high": all_time_high,
        "all_time_high_date": all_time_high_date,
        "week52_high": week52_high,
        "week52_high_date": week52_high_date,
        "currency": currency or "USD"
    }


def fetch_stock_name(ticker):
    """
    종목명(회사명)을 가져옵니다.
    1순위: 토스증권 Open API (종목 마스터 조회)
    2순위: Yahoo Finance chart API의 meta.longName / meta.shortName
    실패 시 티커를 그대로 반환합니다.
    """
    ticker = ticker.strip().upper()

    # 1순위: 토스증권 Open API
    if toss_api.is_configured():
        try:
            toss_symbol = toss_api.to_toss_symbol(ticker)
            info_map = toss_api.fetch_stock_names([toss_symbol])
            if info_map:
                info = info_map.get(toss_symbol) or {}
                name = info.get("name") or info.get("englishName")
                if name:
                    return name
        except Exception as e:
            print(f"Error fetching stock name via Toss for {ticker}: {e}")

    # 2순위: Yahoo Finance
    encoded_ticker = urllib.parse.quote(ticker)
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded_ticker}?range=1d&interval=1d"
    
    try:
        data = _make_request(url)
        if data:
            result = data.get("chart", {}).get("result", [{}])[0]
            meta = result.get("meta", {})
            name = meta.get("longName") or meta.get("shortName")
            if name:
                return name
    except Exception as e:
        print(f"Error fetching stock name for {ticker}: {e}")
    
    return ticker


def _fetch_stock_data_toss(ticker, price_cache=None):
    """
    토스증권 Open API로 일봉 + 현재가 + 종목명을 가져옵니다.
    실패하거나 키가 없으면 None을 반환해 Yahoo Finance로 자동 폴백합니다.
    """
    try:
        daily = toss_api.build_daily_data(ticker, count=260)
        if not daily:
            return None

        toss_symbol = toss_api.to_toss_symbol(ticker)
        price_info = None
        if isinstance(price_cache, dict) and price_cache.get(ticker):
            price_info = price_cache[ticker]
        if not price_info:
            prices = toss_api.fetch_current_prices([toss_symbol])
            if prices and prices.get(toss_symbol):
                p = prices[toss_symbol]
                if p.get("price") is not None:
                    price_info = {"price": p["price"], "currency": p.get("currency")}

        current_price = price_info.get("price") if price_info else None
        if current_price is None:
            current_price = daily["closes"][-1]

        currency = (price_info or {}).get("currency") or daily.get("currency") or "USD"
        previous_close = daily.get("previous_close")
        if not toss_api._is_korean_ticker(ticker):
            session_date = toss_api._market_local_date_str(time.time(), ticker)
            previous_close = _get_us_previous_close(
                ticker, None, session_date, zip(daily["timestamps"], daily["closes"])
            )

        return {
            "ticker": ticker,
            "name": fetch_stock_name(ticker),
            "currency": currency,
            "current_price": current_price,
            "previous_close": previous_close,
            "market_state": daily.get("market_state") or "UNKNOWN",
            "timestamps": daily["timestamps"],
            "closes": daily["closes"],
            "highs": daily["highs"],
            "lows": daily["lows"],
            "opens": daily["opens"],
            "volumes": daily["volumes"]
        }
    except Exception as e:
        print(f"Toss API fetch_stock_data failed for {ticker}: {e}")
        return None


def fetch_stock_data(ticker, price_cache=None):
    """
    종목의 일봉 데이터 + 실시간 현재가 + 종목명을 가져옵니다.
    - 토스증권 Open API가 설정되어 있으면 토스 API를 우선 사용합니다 (국내 서버, 더 빠름).
    - 토스 설정이 없거나 실패하면 Yahoo Finance API로 자동 폴백합니다.

    Returns a dictionary of cleaned stock data or None if failed.
    """
    ticker = ticker.strip().upper()

    # 1순위: 토스증권 Open API
    if toss_api.is_configured():
        toss_data = _fetch_stock_data_toss(ticker, price_cache)
        if toss_data:
            return toss_data

    # 2순위: Yahoo Finance - 일봉 데이터 (기술적 지표 계산용 + 전일종가)
    daily = _get_daily_data(ticker)
    if not daily:
        return None
    
    # 이번 조회에서 이미 받은 배치 가격은 Yahoo 폴백에서도 재사용합니다.
    cached = (price_cache or {}).get(ticker) or {}
    if cached.get("price") is not None:
        realtime_price = cached["price"]
        realtime_prev_close = cached.get("previous_close")
        currency = cached.get("currency")
        market_state = cached.get("market_state") or daily.get("market_state")
    else:
        realtime_price, realtime_prev_close, currency, market_state = _get_realtime_price(ticker)
    
    # currency가 None이면 일봉 데이터에서 가져옴
    if currency is None:
        currency = daily.get("currency", "USD")
    
    # 현재가 결정
    current_price = realtime_price
    if current_price is None:
        current_price = daily["closes"][-1]
    
    # 전일 종가: _get_daily_data에서 시장 현지 날짜 기준으로 계산
    previous_close = daily.get("previous_close")
    if previous_close is None:
        previous_close = realtime_prev_close
    
    # 종목명 가져오기
    stock_name = daily.get("name") or fetch_stock_name(ticker)
    
    return {
        "ticker": ticker,
        "name": stock_name,
        "currency": currency,
        "current_price": current_price,
        "previous_close": previous_close,
        "market_state": market_state or "UNKNOWN",
        "timestamps": daily["timestamps"],
        "closes": daily["closes"],
        "highs": daily["highs"],
        "lows": daily["lows"],
        "opens": daily["opens"],
        "volumes": daily["volumes"]
    }


def fetch_stock_data_intraday(ticker, interval="5m", range_str="5d"):
    """
    5분봉(기본) 단기 데이터를 가져와서 기술적 지표 분석에 사용합니다.
    단기 트레이딩(1~2일) 예측에 적합합니다.
    
    interval: 5m, 15m, 30m, 60m, 1h
    range_str: 1d, 5d, 1mo (interval에 따라 자동 조정 권장)
    
    Returns a dictionary compatible with predictor functions, or None if failed.
    """
    ticker = ticker.strip().upper()
    
    # 1. 분봉 데이터
    intraday = _get_intraday_data(ticker, interval=interval, range_str=range_str)
    if not intraday:
        return None
    
    # 2. 현재가 (마지막 유효 close)
    closes = intraday["closes"]
    current_price = closes[-1] if closes else None
    if current_price is None:
        return None
    
    currency = intraday.get("currency", "USD")
    
    # interval에 따른 봉 이름
    interval_names = {
        "5m": "5분봉", "15m": "15분봉", "30m": "30분봉",
        "60m": "60분봉", "1h": "1시간봉"
    }
    candle_name = interval_names.get(interval, f"{interval}봉")
    
    return {
        "ticker": ticker,
        "currency": currency,
        "current_price": current_price,
        "timeframe": "intraday",
        "candle_name": candle_name,
        "interval": interval,
        "timestamps": intraday["timestamps"],
        "closes": intraday["closes"],
        "highs": intraday["highs"],
        "lows": intraday["lows"],
        "opens": intraday["opens"],
        "volumes": intraday["volumes"]
    }


def fetch_stock_data_weekly(ticker):
    """
    주봉 데이터를 가져와서 기술적 지표 분석에 사용합니다.
    장기 트레이딩(1~3개월) 예측에 적합합니다.
    
    Returns a dictionary compatible with predictor functions, or None if failed.
    """
    ticker = ticker.strip().upper()
    
    # 1. 주봉 데이터
    weekly = _get_weekly_data(ticker)
    if not weekly:
        return None
    
    # 2. 현재가 (마지막 유효 close)
    closes = weekly["closes"]
    current_price = closes[-1] if closes else None
    if current_price is None:
        return None
    
    currency = weekly.get("currency", "USD")
    
    return {
        "ticker": ticker,
        "currency": currency,
        "current_price": current_price,
        "timeframe": "weekly",
        "candle_name": "주봉",
        "interval": "1wk",
        "timestamps": weekly["timestamps"],
        "closes": weekly["closes"],
        "highs": weekly["highs"],
        "lows": weekly["lows"],
        "opens": weekly["opens"],
        "volumes": weekly["volumes"]
    }


def fetch_weekly_change(ticker):
    """
    지난 1주일간(월~금) 종목의 주간 변동률을 계산합니다.
    일봉 데이터(range=60d)에서 지난주 월요일 시가와 금요일 종가를 추출합니다.

    반환: {
        ticker, name, currency,
        week_start_date, week_end_date,
        week_start_price, week_end_price,
        change, change_pct
    } 또는 None
    """
    ticker = ticker.strip().upper()

    # 일봉 데이터 가져오기 (60일치)
    daily = _get_daily_data(ticker)
    if not daily or len(daily["closes"]) < 10:
        return None

    timestamps = daily["timestamps"]
    closes = daily["closes"]
    opens = daily["opens"]

    # KST 기준 날짜 변환
    def _kst_date(ts):
        return time.strftime("%Y-%m-%d", time.gmtime(ts + 9 * 60 * 60))

    def _kst_weekday(ts):
        # 0=월요일 ... 6=일요일 (KST 기준)
        return time.gmtime(ts + 9 * 60 * 60).tm_wday

    # 지난주(월~금) 데이터 찾기
    # 현재 날짜 기준으로 지난주 월요일~금요일 범위 계산
    now_kst = time.gmtime(time.time() + 9 * 60 * 60)
    today_weekday = now_kst.tm_wday  # 0=월요일

    # 이번 주 월요일 날짜 (KST)
    this_monday_ts = time.time() - (today_weekday * 86400)
    this_monday = time.gmtime(this_monday_ts + 9 * 60 * 60)
    this_monday_str = time.strftime("%Y-%m-%d", this_monday)

    # 지난주 월요일~금요일
    last_monday_ts = this_monday_ts - 7 * 86400
    last_friday_ts = this_monday_ts - 3 * 86400  # 금요일 = 월요일 + 4일

    last_monday_str = time.strftime("%Y-%m-%d", time.gmtime(last_monday_ts + 9 * 60 * 60))
    last_friday_str = time.strftime("%Y-%m-%d", time.gmtime(last_friday_ts + 9 * 60 * 60))

    # 지난주 월요일 시가와 금요일 종가 찾기
    week_start_price = None
    week_end_price = None
    week_start_date = None
    week_end_date = None

    for i in range(len(timestamps)):
        date_str = _kst_date(timestamps[i])
        if date_str == last_monday_str:
            week_start_price = opens[i] if i < len(opens) and opens[i] is not None else closes[i]
            week_start_date = date_str
        if date_str == last_friday_str:
            week_end_price = closes[i]
            week_end_date = date_str

    # 금요일 데이터가 없으면 (휴장 등) 가장 가까운 이전 거래일 사용
    if week_end_price is None:
        for i in range(len(timestamps) - 1, -1, -1):
            date_str = _kst_date(timestamps[i])
            if date_str < last_friday_str and date_str >= last_monday_str:
                week_end_price = closes[i]
                week_end_date = date_str
                break

    # 월요일 데이터가 없으면 (휴장 등) 가장 가까운 이후 거래일 사용
    if week_start_price is None:
        for i in range(len(timestamps)):
            date_str = _kst_date(timestamps[i])
            if date_str > last_monday_str and date_str <= last_friday_str:
                week_start_price = opens[i] if i < len(opens) and opens[i] is not None else closes[i]
                week_start_date = date_str
                break

    if week_start_price is None or week_end_price is None or week_start_price <= 0:
        return None

    change = week_end_price - week_start_price
    change_pct = (change / week_start_price) * 100

    # 종목명 가져오기
    stock_name = daily.get("name") or fetch_stock_name(ticker)
    currency = daily.get("currency", "USD")

    return {
        "ticker": ticker,
        "name": stock_name,
        "currency": currency,
        "week_start_date": week_start_date,
        "week_end_date": week_end_date,
        "week_start_price": round(week_start_price, 2),
        "week_end_price": round(week_end_price, 2),
        "change": round(change, 2),
        "change_pct": round(change_pct, 2)
    }


def _fetch_current_price_only_toss(ticker, price_cache=None):
    """
    토스증권 Open API로 현재가를 조회합니다. 실패 시 None을 반환해 Yahoo로 폴백합니다.
    """
    try:
        daily = toss_api.build_daily_data(ticker, count=260)
        daily_prev_close = daily.get("previous_close") if daily else None
        if not toss_api._is_korean_ticker(ticker):
            session_date = toss_api._market_local_date_str(time.time(), ticker)
            daily_prev_close = _get_us_previous_close(
                ticker, None, session_date,
                zip(daily["timestamps"], daily["closes"]) if daily else [],
            )

        toss_symbol = toss_api.to_toss_symbol(ticker)
        price_info = None
        if isinstance(price_cache, dict) and price_cache.get(ticker):
            price_info = price_cache[ticker]
        if not price_info:
            prices = toss_api.fetch_current_prices([toss_symbol])
            if prices and prices.get(toss_symbol):
                p = prices[toss_symbol]
                if p.get("price") is not None:
                    price_info = {"price": p["price"], "currency": p.get("currency")}

        if price_info and price_info.get("price") is not None:
            return {
                "price": price_info["price"],
                "previous_close": daily_prev_close,
                "currency": price_info.get("currency") or (daily.get("currency") if daily else None) or "USD"
            }

        # 현재가 없으면 일봉 마지막 종가로 폴백
        if daily and daily["closes"]:
            return {
                "price": daily["closes"][-1],
                "previous_close": daily_prev_close,
                "currency": daily.get("currency", "USD")
            }
    except Exception as e:
        print(f"Toss API fetch_current_price_only failed for {ticker}: {e}")
    return None


def fetch_current_price_only(ticker, price_cache=None, allow_toss=True):
    """
    가벼운 현재가 조회용 함수.
    - 토스증권 Open API 설정 시 토스로 우선 조회 (배치 현재가, 국내 서버, 더 빠름)
    - 미설정/실패 시 기존 Yahoo Finance 1분봉/5분봉 API로 조회
    - allow_toss=False 인 경우 토스를 건너뛰고 바로 Yahoo Finance로 조회합니다.
    반환: { price, previous_close, currency }
    """
    ticker = ticker.strip().upper()

    # 1순위: 토스증권 Open API
    if allow_toss and toss_api.is_configured():
        toss_price = _fetch_current_price_only_toss(ticker, price_cache)
        if toss_price:
            return toss_price

    # 2순위: Yahoo Finance - 일봉 데이터에서 전일 종가 조회 (가장 정확한 기준)
    daily = _get_daily_data(ticker)
    daily_prev_close = daily.get("previous_close") if daily else None
    
    # 2. 실시간 현재가 (1분봉 + 5분봉 API, 프리장/애프터장 포함)
    price, _, currency, _ = _get_realtime_price(ticker)
    
    if price is not None:
        return {
            "price": price,
            "previous_close": daily_prev_close,
            "currency": currency or "USD"
        }
    
    # 3. intraday 실패 시: 일봉 데이터로 fallback
    if daily and daily["closes"]:
        return {
            "price": daily["closes"][-1],
            "previous_close": daily_prev_close,
            "currency": daily.get("currency", "USD")
        }
    
    return None
