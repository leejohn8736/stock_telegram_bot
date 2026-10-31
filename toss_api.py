"""
Toss Securities Open API integration.
Uses TOSS_CLIENT_ID/TOSS_CLIENT_SECRET from .env for domestic/US stock data.
Falls back to Yahoo Finance if not configured or on failure.
"""

import os
import time
import json
import ssl
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone, timedelta

import config  # noqa: F401

API_BASE = "https://openapi.tossinvest.com"

_token = {"access_token": None, "expires_at": 0, "fail_until": 0}

_HTTP_TIMEOUT = 10
_MAX_RETRIES = 3


def is_configured():
    """토스증권 Open API 키(.env: TOSS_CLIENT_ID / TOSS_CLIENT_SECRET) 설정 여부"""
    return bool(os.environ.get("TOSS_CLIENT_ID")) and bool(os.environ.get("TOSS_CLIENT_SECRET"))


def check_connection():
    """
    실행 시점에 토스 Open API 연동 상태를 확인합니다.
    반환: {"configured": bool, "ok": bool, "message": str}
    """
    if not is_configured():
        return {
            "configured": False,
            "ok": False,
            "message": "TOSS_CLIENT_ID / TOSS_CLIENT_SECRET 미설정 → Yahoo Finance 사용",
        }
    token = _get_access_token(force=True)
    if not token:
        return {
            "configured": True,
            "ok": False,
            "message": "토큰 발급 실패 (client_id/secret 확인 또는 WTS 설정 > Open API > 허용 IP 등록 필요)",
        }
    return {
        "configured": True,
        "ok": True,
        "message": "토큰 발급 성공 → 토스증권 Open API 사용",
    }


def to_toss_symbol(ticker):
    """
    Yahoo 스타일 티커 → 토스증권 symbol 변환.
    - '005930.KS' / '247540.KQ' → '005930' / '247540'
    - 'AAPL', 'TSLA', 'BRK-B' → 그대로
    """
    if not ticker:
        return None
    s = ticker.strip().upper()
    if s.endswith(".KS") or s.endswith(".KQ"):
        return s[:-3]
    return s


def _is_korean_ticker(ticker):
    """한국(KRX) 종목 여부 판별 (Yahoo 티커의 .KS/.KQ 또는 6자리 숫자)"""
    s = (ticker or "").strip().upper()
    if s.endswith(".KS") or s.endswith(".KQ"):
        return True
    return s.isdigit() and len(s) == 6


def _chunks(seq, size):
    """시퀀스를 size 크기 청크로 나눠 순회"""
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def _parse_iso_to_epoch(value):
    """ISO 8601 문자열 → epoch(UTC 초). 파싱 불가 시 None."""
    if not value:
        return None
    try:
        s = str(value).strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except (ValueError, TypeError):
        return None


def _us_eastern_offset_hours(epoch):
    """
    미국 동부 시간대 오프셋(시간) 근사.
    일광절약시간(EDT, UTC-4): 3월 둘째 일요일 07:00 UTC ~ 11월 첫째 일요일 06:00 UTC
    그 외(EST, UTC-5).
    """
    dt = datetime.fromtimestamp(epoch, timezone.utc)
    year = dt.year

    def _nth_sunday(y, m, n):
        d = datetime(y, m, 1, tzinfo=timezone.utc)
        while d.weekday() != 6:
            d += timedelta(days=1)
        return d + timedelta(weeks=n - 1)

    dst_start = _nth_sunday(year, 3, 2).replace(hour=7, minute=0, second=0, microsecond=0)
    dst_end = _nth_sunday(year, 11, 1).replace(hour=6, minute=0, second=0, microsecond=0)
    return -4 if dst_start <= dt < dst_end else -5


def _market_local_date_str(epoch, ticker):
    """epoch(UTC)을 해당 시장(한국 +9 / 미국 동부)의 현지 날짜 문자열로 변환"""
    if _is_korean_ticker(ticker):
        offset_hours = 9
    else:
        offset_hours = _us_eastern_offset_hours(epoch)
    return datetime.fromtimestamp(epoch + offset_hours * 3600, timezone.utc).strftime("%Y-%m-%d")


def _get_access_token(force=False):
    """
    OAuth2 Client Credentials 액세스 토큰을 얻습니다 (만료 전까지 캐싱).
    발급 실패 시 None.
    """
    now = time.time()
    cached = _token["access_token"]
    if cached and not force and _token["expires_at"] > now + 30:
        return cached
    # 직전 발급 실패 시 60초 동안 재시도하지 않음 (유효하지 않은 키로 인한 반복 요청 방지)
    if not force and _token["fail_until"] > now:
        return None
    if not is_configured():
        return None

    payload = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": os.environ.get("TOSS_CLIENT_ID", ""),
        "client_secret": os.environ.get("TOSS_CLIENT_SECRET", ""),
    }).encode("utf-8")

    req = urllib.request.Request(API_BASE + "/oauth2/token", data=payload, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    req.add_header("Accept", "application/json")

    try:
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, context=ctx, timeout=_HTTP_TIMEOUT) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"[toss_api] 토큰 발급 실패: {e}")
        _token["fail_until"] = time.time() + 60
        return None

    token = (body or {}).get("access_token")
    if not token:
        print("[toss_api] 토큰 발급 응답에 access_token 없음")
        _token["fail_until"] = time.time() + 60
        return None

    try:
        expires_in = int((body or {}).get("expires_in", 3600))
    except (TypeError, ValueError):
        expires_in = 3600

    _token["access_token"] = token
    _token["expires_at"] = time.time() + max(expires_in, 60)
    return token


def _api_get(path, params=None):
    """
    인증 헤더(Authorization: Bearer)를 포함한 GET 요청.
    401 → 토큰 재발급 후 1회 재시도, 429 → Retry-After/지수 백오프 재시도.
    실패 시 None 반환 (호출부에서 Yahoo 폴백 가능).
    """
    if not is_configured():
        return None

    token = _get_access_token()
    if not token:
        return None

    query = ""
    if params:
        # quote_via=quote : '+' 등 특수문자까지 정확히 인코딩 (before 파라미터 타임존 오프셋용)
        query = "?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    url = API_BASE + path + query

    backoff = 1.0
    for attempt in range(_MAX_RETRIES):
        req = urllib.request.Request(url)
        req.add_header("Authorization", "Bearer " + token)
        req.add_header("Accept", "application/json")
        try:
            ctx = ssl.create_default_context()
            with urllib.request.urlopen(req, context=ctx, timeout=_HTTP_TIMEOUT) as resp:
                body = resp.read().decode("utf-8")
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as e:
            if e.code == 401 and attempt == 0:
                # 토큰 만료 가능성 → 재발급 후 1회 재시도
                token = _get_access_token(force=True)
                if not token:
                    return None
                continue
            if e.code == 429:
                retry_after = None
                try:
                    retry_after = float(e.headers.get("Retry-After"))
                except (TypeError, ValueError):
                    retry_after = None
                wait = retry_after if (retry_after and retry_after > 0) else backoff
                time.sleep(wait)
                backoff *= 2
                continue
            if e.code == 403:
                print("[toss_api] 403 Forbidden - WTS 설정 > Open API > 허용 IP를 확인하세요. "
                      "(Yahoo Finance로 폴백합니다)")
                return None
            if attempt < _MAX_RETRIES - 1:
                time.sleep(backoff)
                backoff *= 2
                continue
            return None
        except Exception:
            if attempt < _MAX_RETRIES - 1:
                time.sleep(backoff)
                backoff *= 2
                continue
            return None
    return None


def fetch_current_prices(symbols):
    """
    복수 종목 현재가 일괄 조회 (최대 200종목/요청).
    반환: {symbol: {"price": float|None, "timestamp": str|None, "currency": str|None}}
          또는 실패 시 None
    """
    symbols = [s for s in (symbols or []) if s]
    if not symbols or not is_configured():
        return None

    result = {}
    for chunk in _chunks(symbols, 200):
        data = _api_get("/api/v1/prices", {"symbols": ",".join(chunk)})
        if not data:
            return None
        for item in data.get("result", []) or []:
            symbol = item.get("symbol")
            if not symbol:
                continue
            price = None
            try:
                if item.get("lastPrice") is not None:
                    price = float(item["lastPrice"])
            except (TypeError, ValueError):
                price = None
            result[symbol] = {
                "price": price,
                "timestamp": item.get("timestamp"),
                "currency": item.get("currency"),
            }
    return result or None


def fetch_candles(symbol, interval="1d", count=200, before=None):
    """
    캔들 OHLCV 조회.
    - interval: '1m' | '1d'
    - count: 최대 200
    - before: ISO 8601 페이지네이션 상한 (이전 응답의 nextBefore 사용)
    반환: {"candles": [...], "next_before": str|None} 또는 실패 시 None
    """
    if not symbol or not is_configured():
        return None
    if interval not in ("1m", "1d"):
        return None

    params = {"symbol": symbol, "interval": interval, "count": max(1, min(200, int(count)))}
    if before:
        params["before"] = before

    data = _api_get("/api/v1/candles", params)
    if not data:
        return None
    result = data.get("result") or {}
    return {
        "candles": result.get("candles") or [],
        "next_before": result.get("nextBefore"),
    }


def fetch_daily_candles(symbol, count=260):
    """
    일봉 캔들을 count개만큼 페이지네이션으로 모아 최신순 리스트로 반환.
    """
    candles = []
    before = None
    while len(candles) < count:
        page = fetch_candles(symbol, interval="1d",
                             count=min(200, count - len(candles)), before=before)
        if not page:
            break
        batch = page["candles"]
        if not batch:
            break
        candles.extend(batch)
        nxt = page.get("next_before")
        if not nxt or nxt == before:
            break
        before = nxt
    return candles


def build_daily_data(ticker, count=260):
    """
    토스 일봉 데이터를 stock_api._get_daily_data() 와 호환되는 dict 로 변환.
    반환:
      {timestamps, closes, highs, lows, opens, volumes, currency,
       previous_close, market_state} 또는 실패 시 None
    """
    symbol = to_toss_symbol(ticker)
    if not symbol:
        return None

    candles = fetch_daily_candles(symbol, count=count)
    if not candles:
        return None

    cleaned = {"timestamps": [], "closes": [], "highs": [], "lows": [], "opens": [], "volumes": []}

    # candles 는 최신순 → 과거→최신 오름차순으로 뒤집어 저장
    for c in reversed(candles):
        if not isinstance(c, dict):
            continue
        try:
            o = float(c["openPrice"])
            h = float(c["highPrice"])
            l = float(c["lowPrice"])
            cl = float(c["closePrice"])
        except (KeyError, TypeError, ValueError):
            continue
        ts = _parse_iso_to_epoch(c.get("timestamp"))
        if ts is None:
            continue
        try:
            v = float(c.get("volume") or 0)
        except (TypeError, ValueError):
            v = 0.0

        cleaned["timestamps"].append(ts)
        cleaned["opens"].append(o)
        cleaned["highs"].append(h)
        cleaned["lows"].append(l)
        cleaned["closes"].append(cl)
        cleaned["volumes"].append(v)

    if len(cleaned["closes"]) < 20:
        return None

    # 통화: 최신 캔들의 currency 값 사용 (없으면 stock_info 에서 채움)
    currency = None
    for c in candles:
        if isinstance(c, dict) and c.get("currency"):
            currency = c.get("currency")
            break
    cleaned["currency"] = currency
    cleaned["previous_close"] = _compute_previous_close(cleaned, ticker)
    cleaned["market_state"] = market_state(ticker)
    return cleaned


def _compute_previous_close(cleaned, ticker):
    """
    전일 종가 계산 (Yahoo 경로의 currentTradingPeriod 로직과 동일한 의도).
    - 마지막 캔들이 오늘(해당 시장 기준) 세션 캔들이면 → 그 이전 캔들 종가
    - 아니면 (휴장/장 전) 마지막 캔들 종가
    """
    try:
        if cleaned["timestamps"]:
            last_epoch = cleaned["timestamps"][-1]
            last_date = _market_local_date_str(last_epoch, ticker)
            today = _market_local_date_str(time.time(), ticker)
            if last_date == today and len(cleaned["closes"]) >= 2:
                return cleaned["closes"][-2]
    except Exception:
        pass
    return cleaned["closes"][-1] if cleaned["closes"] else None


def fetch_stock_names(symbols):
    """
    복수 종목 기본 정보 조회 (최대 200종목/요청).
    반환: {symbol: {name, englishName, market, currency, ...}} 또는 실패 시 None
    """
    symbols = [s for s in (symbols or []) if s]
    if not symbols or not is_configured():
        return None

    result = {}
    for chunk in _chunks(symbols, 200):
        data = _api_get("/api/v1/stocks", {"symbols": ",".join(chunk)})
        if not data:
            return None
        for item in data.get("result", []) or []:
            if item.get("symbol"):
                result[item["symbol"]] = item
    return result or None


def market_state(ticker):
    """
    토스 API 는 marketState 를 직접 제공하지 않아, 시장 시각 기반으로 근사 추정합니다.
    PRE / REGULAR / POST / CLOSED / UNKNOWN 중 하나를 반환합니다.
    """
    now = time.time()
    try:
        if _is_korean_ticker(ticker):
            local = datetime.fromtimestamp(now + 9 * 3600, timezone.utc)
            t = local.hour * 60 + local.minute
            if local.weekday() >= 5:
                return "CLOSED"
            if 9 * 60 <= t <= 15 * 60 + 30:
                return "REGULAR"
            if 15 * 60 + 30 < t <= 16 * 60 + 30:
                return "POST"
            return "CLOSED"

        # 미국
        offset = _us_eastern_offset_hours(now)
        local = datetime.fromtimestamp(now + offset * 3600, timezone.utc)
        t = local.hour * 60 + local.minute
        if local.weekday() >= 5:
            return "CLOSED"
        if 4 * 60 <= t < 9 * 60 + 30:
            return "PRE"
        if 9 * 60 + 30 <= t <= 16 * 60:
            return "REGULAR"
        if 16 * 60 < t <= 20 * 60:
            return "POST"
        return "CLOSED"
    except Exception:
        return "UNKNOWN"


if __name__ == "__main__":
    print("===== Toss Open API 상태 확인 =====")
    status = check_connection()
    print(f"- 설정 여부    : {status['configured']}")
    print(f"- 연결 상태    : {'OK' if status['ok'] else 'FAIL'}")
    print(f"- 메시지       : {status['message']}")

    if status["ok"]:
        print(f"- to_toss_symbol('005930.KS') = {to_toss_symbol('005930.KS')}")
        print(f"- to_toss_symbol('AAPL')      = {to_toss_symbol('AAPL')}")

        # 현재가 배치 조회 테스트
        prices = fetch_current_prices(["AAPL", "005930"])
        print(f"- 현재가 조회    : {prices}")

        # 일봉 데이터 테스트
        daily = build_daily_data("AAPL", count=60)
        if daily:
            print(f"- AAPL 일봉     : {len(daily['closes'])}건, 최신 종가 {daily['closes'][-1]}, "
                  f"전일 종가 {daily['previous_close']}, 통화 {daily['currency']}, 상태 {daily['market_state']}")

        # 종목명 조회 테스트
        names = fetch_stock_names(["AAPL", "005930"])
        print(f"- 종목명 조회    : {names}")
