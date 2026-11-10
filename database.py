from __future__ import annotations

import os
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

def add_subscription(chat_id: int, ticker: str) -> bool:
    """Subscribe user to ticker. Returns True if added, False if already exists."""
    ticker = ticker.upper().strip()
    if not supabase:
        return False
    try:
        # 중복 확인
        res = supabase.table("subscriptions").select("*").eq("chat_id", chat_id).eq("ticker", ticker).execute()
        if res.data and len(res.data) > 0:
            return False
        
        # 추가
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
        res = supabase.table("subscriptions").select("*").eq("chat_id", chat_id).eq("ticker", ticker).execute()
        if not res.data or len(res.data) == 0:
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
            tickers = [s["ticker"] for s in res.data]
            return sorted(list(set(tickers)))
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
            tickers = [s["ticker"] for s in res.data]
            return sorted(list(set(tickers)))
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

# --- 부가 기능 호환 함수들 (에러 방지용) ---
def get_last_signal(chat_id, ticker, signal_type):
    return None

def set_last_signal(chat_id, ticker, signal_type, price):
    pass

def clear_last_signal(chat_id, ticker, signal_type):
    return False

def clear_all_signals_for_ticker(chat_id, ticker):
    pass

def set_last_price(chat_id, ticker, price):
    pass

def get_daily_alerts_for_date(chat_id, ticker, alert_date):
    return []

def record_daily_alert(chat_id, ticker, alert_date, threshold_pct, direction):
    return True

def has_sent_weekly_events(chat_id, week_start):
    return False

def record_weekly_events_send(chat_id, week_start):
    pass

def has_sent_weekly_report(chat_id, week_start):
    return False

def record_weekly_report_send(chat_id, week_start):
    return True

def set_chat_topic(chat_id, message_thread_id):
    pass

def clear_chat_topic(chat_id):
    pass

def get_chat_topic(chat_id):
    return None

ALERT_LEVELS = ("OFF", "MARKET", "IMPORTANT", "ALL")
ALERT_LEVEL_LABELS = {
    "OFF": "🔕 모든 알람 받지 않음",
    "MARKET": "🌍 시장 알림만 (장마감 요약 등)",
    "IMPORTANT": "⭐ 중요 알림 + 시장 알림",
    "ALL": "🔔 모든 알람 받기",
}

def set_chat_alert_level(chat_id, level):
    return True

def get_chat_alert_level(chat_id):
    return "ALL"

def should_send_alert(chat_id, is_market_wide=False, is_important=False):
    return True

def set_chat_alerts_enabled(chat_id, enabled):
    return True

def get_chat_alerts_enabled(chat_id):
    return True

def has_sent_signal_alert(chat_id, ticker, signal_type, alert_date):
    return False

def record_signal_alert(chat_id, ticker, signal_type, alert_date, price=None):
    return True

def has_sent_recommendation_alert(chat_id, ticker, alert_date, alert_type):
    return False

def record_recommendation_alert(chat_id, ticker, alert_date, alert_type, price):
    return True
