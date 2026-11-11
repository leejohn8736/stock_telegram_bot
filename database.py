from __future__ import annotations

import os
import threading
import time
from supabase import create_client, Client

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

supabase: Client | None = None
if SUPABASE_URL and SUPABASE_KEY:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception as e:
        print(f"[Supabase Init Error] {e}")


def init_db():
    """Supabase 초기화 확인"""
    if not supabase:
        print("⚠️ Supabase client not initialized. Check SUPABASE_URL and SUPABASE_KEY.")
    else:
        print("✅ Supabase client connected successfully.")


# ================================================================
# 구독 (subscriptions)
# ================================================================

def add_subscription(chat_id: int, ticker: str) -> bool:
    """Subscribe user to ticker. Returns True if added, False if already exists."""
    ticker = ticker.upper().strip()
    if not supabase:
        return False
    try:
        res = supabase.table("subscriptions").select("ticker").eq("chat_id", chat_id).eq("ticker", ticker).execute()
        if res.data and len(res.data) > 0:
            return False
        supabase.table("subscriptions").insert({"chat_id": chat_id, "ticker": ticker}).execute()
        return True
    except Exception as e:
        print(f"[Supabase Error] add_subscription: {e}")
        return False


def remove_subscription(chat_id: int, ticker: str) -> bool:
    """Unsubscribe user from ticker. Returns True if deleted."""
    ticker = ticker.upper().strip()
    if not supabase:
        return False
    try:
        res = supabase.table("subscriptions").select("ticker").eq("chat_id", chat_id).eq("ticker", ticker).execute()
        if not res.data:
            return False
        supabase.table("subscriptions").delete().eq("chat_id", chat_id).eq("ticker", ticker).execute()
        return True
    except Exception as e:
        print(f"[Supabase Error] remove_subscription: {e}")
        return False


def get_user_subscriptions(chat_id: int) -> list[str]:
    """Get all tickers a user is subscribed to."""
    if not supabase:
        return []
    try:
        res = supabase.table("subscriptions").select("ticker").eq("chat_id", chat_id).execute()
        if res.data:
            return sorted(set(s["ticker"] for s in res.data))
    except Exception as e:
        print(f"[Supabase Error] get_user_subscriptions: {e}")
    return []


def get_all_subscriptions() -> list[tuple[int, str]]:
    """Get all subscription mappings: [(chat_id, ticker), ...]"""
    if not supabase:
        return []
    try:
        res = supabase.table("subscriptions").select("chat_id, ticker").execute()
        if res.data:
            return [(s["chat_id"], s["ticker"]) for s in res.data]
    except Exception as e:
        print(f"[Supabase Error] get_all_subscriptions: {e}")
    return []


def get_unique_tickers() -> list[str]:
    """Get all unique tickers with at least one subscriber."""
    if not supabase:
        return []
    try:
        res = supabase.table("subscriptions").select("ticker").execute()
        if res.data:
            return sorted(set(s["ticker"] for s in res.data))
    except Exception as e:
        print(f"[Supabase Error] get_unique_tickers: {e}")
    return []


def get_subscribers_for_ticker(ticker: str) -> list[int]:
    """Get all chat IDs subscribed to a ticker."""
    ticker = ticker.upper().strip()
    if not supabase:
        return []
    try:
        res = supabase.table("subscriptions").select("chat_id").eq("ticker", ticker).execute()
        if res.data:
            return [s["chat_id"] for s in res.data]
    except Exception as e:
        print(f"[Supabase Error] get_subscribers_for_ticker: {e}")
    return []


# ================================================================
# 채팅 설정 (chat_settings): 알람 수준 / 알림 on-off / 토픽
# ================================================================

ALERT_LEVELS = ("OFF", "MARKET", "IMPORTANT", "ALL")
ALERT_LEVEL_LABELS = {
    "OFF": "🔕 모든 알람 받지 않음",
    "MARKET": "🌍 시장 알림만 (장마감 요약 등)",
    "IMPORTANT": "⭐ 중요 알림 + 시장 알림",
    "ALL": "🔔 모든 알람 받기",
}

_DEFAULT_SETTINGS = {"alert_level": "ALL", "alerts_enabled": True, "topic_id": None}
_SETTINGS_TTL = 30  # 초. 스케줄러가 매분 호출해도 DB 요청이 폭증하지 않도록 캐시
_settings_cache: dict = {}
_settings_lock = threading.Lock()


def _invalidate_settings(chat_id):
    with _settings_lock:
        _settings_cache.pop(chat_id, None)


def _get_settings(chat_id):
    """
    채팅 설정을 반환합니다.
    - 행이 없으면 기본값(ALL, 켜짐)
    - DB 오류면 None (호출한 쪽에서 안전하게 차단 처리)
    """
    now = time.time()
    with _settings_lock:
        cached = _settings_cache.get(chat_id)
        if cached and now - cached[0] < _SETTINGS_TTL:
            return cached[1]

    if not supabase:
        return None
    try:
        res = supabase.table("chat_settings").select("alert_level, alerts_enabled, topic_id").eq("chat_id", chat_id).execute()
        if res.data:
            row = res.data[0]
            settings = {
                "alert_level": row.get("alert_level") or "ALL",
                "alerts_enabled": True if row.get("alerts_enabled") is None else bool(row.get("alerts_enabled")),
                "topic_id": row.get("topic_id"),
            }
        else:
            settings = dict(_DEFAULT_SETTINGS)
        with _settings_lock:
            _settings_cache[chat_id] = (now, settings)
        return settings
    except Exception as e:
        print(f"[Supabase Error] _get_settings: {e}")
        return None


def _save_settings(chat_id, **fields) -> bool:
    if not supabase:
        return False
    try:
        row = {"chat_id": chat_id}
        row.update(fields)
        supabase.table("chat_settings").upsert(row, on_conflict="chat_id").execute()
        _invalidate_settings(chat_id)
        return True
    except Exception as e:
        print(f"[Supabase Error] _save_settings: {e}")
        return False


def set_chat_alert_level(chat_id, level):
    level = str(level).upper()
    if level not in ALERT_LEVELS:
        return False
    return _save_settings(chat_id, alert_level=level)


def get_chat_alert_level(chat_id):
    settings = _get_settings(chat_id)
    if settings is None:
        return "OFF"  # DB 오류 시 안전하게 차단 (알림 폭탄 방지)
    return settings["alert_level"]


def set_chat_alerts_enabled(chat_id, enabled):
    return _save_settings(chat_id, alerts_enabled=bool(enabled))


def get_chat_alerts_enabled(chat_id):
    settings = _get_settings(chat_id)
    if settings is None:
        return False  # DB 오류 시 안전하게 차단
    return settings["alerts_enabled"]


def should_send_alert(chat_id, is_market_wide=False, is_important=False):
    """
    알림 전송 허용 여부.
    - 알림 꺼짐(/alerts off) 또는 OFF  → 전부 차단
    - MARKET    → 시장 알림만
    - IMPORTANT → 시장 알림 + 중요 종목 알림
    - ALL       → 전부 허용
    DB 오류 시에는 차단합니다.
    """
    settings = _get_settings(chat_id)
    if settings is None:
        return False
    if not settings["alerts_enabled"]:
        return False
    level = settings["alert_level"]
    if level == "OFF":
        return False
    if level == "MARKET":
        return bool(is_market_wide)
    if level == "IMPORTANT":
        return bool(is_market_wide or is_important)
    return True


def set_chat_topic(chat_id, message_thread_id):
    return _save_settings(chat_id, topic_id=int(message_thread_id))


def clear_chat_topic(chat_id):
    return _save_settings(chat_id, topic_id=None)


def get_chat_topic(chat_id):
    settings = _get_settings(chat_id)
    if settings is None:
        return None
    return settings.get("topic_id")


# ================================================================
# 알림 이력 (alert_log): 중복 전송 방지의 핵심
#   kind: 'signal' | 'recommendation' | 'daily' | 'weekly_events' | 'weekly_report'
#   (chat_id, ticker, kind, alert_date, alert_key) 가 UNIQUE
#   → record_* 는 "처음 기록된 경우에만 True" 를 반환합니다.
#   → has_sent_* 는 DB 오류 시 True(이미 보냄)로 간주해 알림 폭탄을 막습니다.
# ================================================================

def _is_duplicate_error(e) -> bool:
    text = str(e).lower()
    return "23505" in text or "duplicate" in text or "unique" in text


def _log_exists(chat_id, ticker, kind, alert_date, alert_key) -> bool:
    """이력 존재 여부. DB 오류 시 True 반환(보내지 않는 쪽으로 안전하게)."""
    if not supabase:
        return True
    try:
        res = (supabase.table("alert_log").select("id")
               .eq("chat_id", chat_id).eq("ticker", ticker).eq("kind", kind)
               .eq("alert_date", str(alert_date)).eq("alert_key", alert_key)
               .limit(1).execute())
        return bool(res.data)
    except Exception as e:
        print(f"[Supabase Error] _log_exists({kind}): {e}")
        return True


def _log_record(chat_id, ticker, kind, alert_date, alert_key, price=None) -> bool:
    """이력을 새로 기록하면 True, 이미 있거나 실패하면 False."""
    if not supabase:
        return False
    try:
        row = {
            "chat_id": chat_id,
            "ticker": ticker,
            "kind": kind,
            "alert_date": str(alert_date),
            "alert_key": alert_key,
        }
        if price is not None:
            row["price"] = float(price)
        supabase.table("alert_log").insert(row).execute()
        return True
    except Exception as e:
        if not _is_duplicate_error(e):
            print(f"[Supabase Error] _log_record({kind}): {e}")
        return False


# --- 기술 신호 알림 (이평선/볼린저/RSI/최고가 등) ---
def has_sent_signal_alert(chat_id, ticker, signal_type, alert_date):
    return _log_exists(chat_id, ticker, "signal", alert_date, str(signal_type))


def record_signal_alert(chat_id, ticker, signal_type, alert_date, price=None):
    return _log_record(chat_id, ticker, "signal", alert_date, str(signal_type), price)


# --- STRONG BUY / STRONG SELL 권장 알림 ---
def has_sent_recommendation_alert(chat_id, ticker, alert_date, alert_type):
    return _log_exists(chat_id, ticker, "recommendation", alert_date, str(alert_type))


def record_recommendation_alert(chat_id, ticker, alert_date, alert_type, price):
    return _log_record(chat_id, ticker, "recommendation", alert_date, str(alert_type), price)


# --- 전일 종가 대비 가격 변동 알림 (5%/10%/20%, 방향별) ---
def _daily_key(threshold_pct, direction):
    return f"{float(threshold_pct)}:{str(direction)}"


def get_daily_alerts_for_date(chat_id, ticker, alert_date):
    """해당 날짜에 이미 보낸 (threshold_pct, direction) 튜플 리스트."""
    if not supabase:
        return []
    try:
        res = (supabase.table("alert_log").select("alert_key")
               .eq("chat_id", chat_id).eq("ticker", ticker).eq("kind", "daily")
               .eq("alert_date", str(alert_date)).execute())
        result = []
        for row in res.data or []:
            threshold, direction = row["alert_key"].split(":", 1)
            result.append((float(threshold), direction))
        return result
    except Exception as e:
        print(f"[Supabase Error] get_daily_alerts_for_date: {e}")
        return []


def record_daily_alert(chat_id, ticker, alert_date, threshold_pct, direction):
    """처음 기록하면 True, 이미 있거나 실패하면 False. 전송 '전에' 호출해야 안전합니다."""
    return _log_record(chat_id, ticker, "daily", alert_date, _daily_key(threshold_pct, direction))


# --- 주간 일정 / 주간 리포트 (chat 단위, ticker 는 빈 문자열) ---
def has_sent_weekly_events(chat_id, week_start):
    return _log_exists(chat_id, "", "weekly_events", week_start, "-")


def record_weekly_events_send(chat_id, week_start):
    return _log_record(chat_id, "", "weekly_events", week_start, "-")


def has_sent_weekly_report(chat_id, week_start):
    return _log_exists(chat_id, "", "weekly_report", week_start, "-")


def record_weekly_report_send(chat_id, week_start):
    return _log_record(chat_id, "", "weekly_report", week_start, "-")


# ================================================================
# 마지막 신호 / 마지막 가격 (last_signals)
#   (chat_id, ticker, signal_type) 가 UNIQUE
# ================================================================

def get_last_signal(chat_id, ticker, signal_type):
    """마지막 신호 정보 dict(price, updated_at) 또는 None."""
    if not supabase:
        return None
    try:
        res = (supabase.table("last_signals").select("price, updated_at")
               .eq("chat_id", chat_id).eq("ticker", ticker).eq("signal_type", str(signal_type))
               .limit(1).execute())
        if res.data:
            return res.data[0]
    except Exception as e:
        print(f"[Supabase Error] get_last_signal: {e}")
    return None


def set_last_signal(chat_id, ticker, signal_type, price):
    if not supabase:
        return False
    try:
        supabase.table("last_signals").upsert({
            "chat_id": chat_id,
            "ticker": ticker,
            "signal_type": str(signal_type),
            "price": None if price is None else float(price),
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }, on_conflict="chat_id,ticker,signal_type").execute()
        return True
    except Exception as e:
        print(f"[Supabase Error] set_last_signal: {e}")
        return False


def clear_last_signal(chat_id, ticker, signal_type):
    if not supabase:
        return False
    try:
        supabase.table("last_signals").delete() \
            .eq("chat_id", chat_id).eq("ticker", ticker).eq("signal_type", str(signal_type)).execute()
        return True
    except Exception as e:
        print(f"[Supabase Error] clear_last_signal: {e}")
        return False


def clear_all_signals_for_ticker(chat_id, ticker):
    """종목 삭제 시 해당 종목의 신호/알림 이력을 정리합니다."""
    if not supabase:
        return False
    try:
        supabase.table("last_signals").delete().eq("chat_id", chat_id).eq("ticker", ticker).execute()
        supabase.table("alert_log").delete().eq("chat_id", chat_id).eq("ticker", ticker).execute()
        return True
    except Exception as e:
        print(f"[Supabase Error] clear_all_signals_for_ticker: {e}")
        return False


def set_last_price(chat_id, ticker, price):
    return set_last_signal(chat_id, ticker, "_last_price", price)


def cleanup_old_alerts(keep_days: int = 14):
    """오래된 알림 이력 삭제 (선택 사항: 스케줄러에서 하루 1번 호출하면 DB가 커지지 않음)."""
    if not supabase:
        return False
    try:
        cutoff = time.strftime("%Y-%m-%d", time.gmtime(time.time() - keep_days * 86400))
        supabase.table("alert_log").delete().lt("alert_date", cutoff).execute()
        return True
    except Exception as e:
        print(f"[Supabase Error] cleanup_old_alerts: {e}")
        return False
