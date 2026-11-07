from __future__ import annotations

import json
import os
import requests
from contextlib import contextmanager
from config import JSONBIN_API_KEY, JSONBIN_BIN_ID

# JSONbin.io API 설정
JSONBIN_URL = f"https://api.jsonbin.io/v3/b/{JSONBIN_BIN_ID}"

def _get_headers() -> dict:
    return {
        "Content-Type": "application/json",
        "X-Master-Key": JSONBIN_API_KEY
    }

def _load_db() -> dict:
    """JSONbin.io에서 전체 데이터 구조를 불러옵니다."""
    if not JSONBIN_API_KEY or not JSONBIN_BIN_ID:
        # 설정이 안 되어있을 경우 기본 빈 구조 반환 (로컬 테스트용 방어 코드)
        return {
            "subscriptions": [], # [ {chat_id, ticker}, ... ]
            "last_signals": [],  # [ {chat_id, ticker, signal_type, last_price, updated_at}, ... ]
            "last_prices": [],   # [ {chat_id, ticker, last_price, last_alert_price, alert_threshold_pct, updated_at}, ... ]
            "daily_price_alerts": [], # [ {chat_id, ticker, alert_date, threshold_pct, direction, created_at}, ... ]
            "chat_topics": [],   # [ {chat_id, message_thread_id, updated_at}, ... ]
            "chat_alert_settings": [], # [ {chat_id, alerts_enabled, alert_level, updated_at}, ... ]
            "daily_signal_alerts": [], # [ {chat_id, ticker, signal_type, alert_date, price, created_at}, ... ]
            "high_breakout_alerts": [], # [ {chat_id, ticker, alert_type, alert_date, price, created_at}, ... ]
            "weekly_report_sends": [], # [ {chat_id, week_start, sent_at}, ... ]
            "weekly_event_sends": [], # [ {chat_id, week_start, sent_at}, ... ]
            "recommendation_alerts": [] # [ {chat_id, ticker, alert_date, alert_type, price, created_at}, ... ]
        }
    
    try:
        response = requests.get(JSONBIN_URL, headers=_get_headers(), timeout=10)
        if response.status_code == 200:
            data = response.json().get("record", {})
            # 필수 키가 누락된 경우 기본값 채우기
            if "subscriptions" not in data:
                data["subscriptions"] = []
            return data
    except Exception as e:
        print(f"[JSONbin Load Error] {e}")
    
    return {}

def _save_db(data: dict):
    """JSONbin.io에 전체 데이터 구조를 통째로 저장(업데이트)합니다."""
    if not JSONBIN_API_KEY or not JSONBIN_BIN_ID:
        return
    try:
        requests.put(JSONBIN_URL, headers=_get_headers(), json=data, timeout=10)
    except Exception as e:
        print(f"[JSONbin Save Error] {e}")


def init_db():
    """초기화 (JSONbin 구조가 비어있다면 기본 틀을 만듭니다.)"""
    data = _load_db()
    if not data:
        data = {
            "subscriptions": [],
            "last_signals": [],
            "last_prices": [],
            "daily_price_alerts": [],
            "chat_topics": [],
            "chat_alert_settings": [],
            "daily_signal_alerts": [],
            "high_breakout_alerts": [],
            "weekly_report_sends": [],
            "weekly_event_sends": [],
            "recommendation_alerts": []
        }
        _save_db(data)

def add_subscription(chat_id, ticker):
    """Subscribe user to ticker. Returns True if added, False if already exists."""
    ticker = ticker.upper().strip()
    data = _load_db()
    
    subs = data.setdefault("subscriptions", [])
    # 중복 확인
    for sub in subs:
        if sub.get("chat_id") == chat_id and sub.get("ticker") == ticker:
            return False
            
    subs.append({"chat_id": chat_id, "ticker": ticker})
    
    # last_prices 초기화 또는 유지
    prices = data.setdefault("last_prices", [])
    exists_price = False
    for p in prices:
        if p.get("chat_id") == chat_id and p.get("ticker") == ticker:
            exists_price = True
            break
    if not exists_price:
        prices.append({
            "chat_id": chat_id,
            "ticker": ticker,
            "last_price": None,
            "last_alert_price": None,
            "alert_threshold_pct": 5.0
        })
        
    _save_db(data)
    return True

def remove_subscription(chat_id, ticker):
    """Unsubscribe user from ticker. Returns True if deleted."""
    ticker = ticker.upper().strip()
    data = _load_db()
    
    subs = data.get("subscriptions", [])
    new_subs = [s for s in subs if not (s.get("chat_id") == chat_id and s.get("ticker") == ticker)]
    removed = len(new_subs) < len(subs)
    data["subscriptions"] = new_subs
    
    # last_prices 제거
    prices = data.get("last_prices", [])
    data["last_prices"] = [p for p in prices if not (p.get("chat_id") == chat_id and p.get("ticker") == ticker)]
    
    if removed:
        _save_db(data)
    return removed

def get_user_subscriptions(chat_id):
    """Get all tickers a user is subscribed to."""
    data = _load_db()
    subs = data.get("subscriptions", [])
    tickers = [s["ticker"] for s in subs if s.get("chat_id") == chat_id]
    return sorted(list(set(tickers)))

def get_all_subscriptions():
    """Get all subscription mappings: [(chat_id, ticker), ...]"""
    data = _load_db()
    subs = data.get("subscriptions", [])
    return [(s["chat_id"], s["ticker"]) for s in subs]

def get_unique_tickers():
    """Get all unique tickers with at least one subscriber."""
    data = _load_db()
    subs = data.get("subscriptions", [])
    return sorted(list(set(s["ticker"] for s in subs)))

def get_subscribers_for_ticker(ticker):
    """Get all chat IDs subscribed to a ticker."""
    ticker = ticker.upper().strip()
    data = _load_db()
    subs = data.get("subscriptions", [])
    return [s["chat_id"] for s in subs if s.get("ticker") == ticker]

# Signal tracking functions to prevent redundant alerts
def get_last_signal(chat_id, ticker, signal_type):
    ticker = ticker.upper().strip()
    data = _load_db()
    signals = data.get("last_signals", [])
    for sig in signals:
        if sig.get("chat_id") == chat_id and sig.get("ticker") == ticker and sig.get("signal_type") == signal_type:
            return {"price": sig.get("last_price"), "updated_at": sig.get("updated_at")}
    return None

def set_last_signal(chat_id, ticker, signal_type, price):
    ticker = ticker.upper().strip()
    data = _load_db()
    signals = data.setdefault("last_signals", [])
    
    # 기존 항목 업데이트 또는 추가
    updated = False
    for sig in signals:
        if sig.get("chat_id") == chat_id and sig.get("ticker") == ticker and sig.get("signal_type") == signal_type:
            sig["last_price"] = price
            sig["updated_at"] = "CURRENT_TIMESTAMP"
            updated = True
            break
    if not updated:
        signals.append({
            "chat_id": chat_id,
            "ticker": ticker,
            "signal_type": signal_type,
            "last_price": price,
            "updated_at": "CURRENT_TIMESTAMP"
        })
    _save_db(data)

def clear_last_signal(chat_id, ticker, signal_type):
    ticker = ticker.upper().strip()
    data = _load_db()
    signals = data.get("last_signals", [])
    new_signals = [sig for sig in signals if not (sig.get("chat_id") == chat_id and sig.get("ticker") == ticker and sig.get("signal_type") == signal_type)]
    removed = len(new_signals) < len(signals)
    data["last_signals"] = new_signals
    if removed:
        _save_db(data)
    return removed

def clear_all_signals_for_ticker(chat_id, ticker):
    ticker = ticker.upper().strip()
    data = _load_db()
    signals = data.get("last_signals", [])
    data["last_signals"] = [sig for sig in signals if not (sig.get("chat_id") == chat_id and sig.get("ticker") == ticker)]
    _save_db(data)

# Price tracking functions for % change alerts
def set_last_price(chat_id, ticker, price):
    ticker = ticker.upper().strip()
    data = _load_db()
    prices = data.setdefault("last_prices", [])
    
    updated = False
    for p in prices:
        if p.get("chat_id") == chat_id and p.get("ticker") == ticker:
            p["last_price"] = price
            p["updated_at"] = "CURRENT_TIMESTAMP"
            updated = True
            break
    if not updated:
        prices.append({
            "chat_id": chat_id,
            "ticker": ticker,
            "last_price": price,
            "last_alert_price": None,
            "alert_threshold_pct": 5.0,
            "updated_at": "CURRENT_TIMESTAMP"
        })
    _save_db(data)

def get_daily_alerts_for_date(chat_id, ticker, alert_date):
    ticker = ticker.upper().strip()
    data = _load_db()
    alerts = data.get("daily_price_alerts", [])
    results = []
    for a in alerts:
        if a.get("chat_id") == chat_id and a.get("ticker") == ticker and a.get("alert_date") == alert_date:
            results.append({"threshold_pct": a.get("threshold_pct"), "direction": a.get("direction")})
    return results

def record_daily_alert(chat_id, ticker, alert_date, threshold_pct, direction):
    ticker = ticker.upper().strip()
    data = _load_db()
    alerts = data.setdefault("daily_price_alerts", [])
    
    for a in alerts:
        if (a.get("chat_id") == chat_id and a.get("ticker") == ticker and 
            a.get("alert_date") == alert_date and a.get("threshold_pct") == threshold_pct and 
            a.get("direction") == direction):
            return False
            
    alerts.append({
        "chat_id": chat_id,
        "ticker": ticker,
        "alert_date": alert_date,
        "threshold_pct": threshold_pct,
        "direction": direction,
        "created_at": "CURRENT_TIMESTAMP"
    })
    _save_db(data)
    return True

# Weekly report tracking
def has_sent_weekly_events(chat_id, week_start):
    data = _load_db()
    events = data.get("weekly_event_sends", [])
    for e in events:
        if e.get("chat_id") == chat_id and e.get("week_start") == week_start:
            return True
    return False

def record_weekly_events_send(chat_id, week_start):
    data = _load_db()
    events = data.setdefault("weekly_event_sends", [])
    for e in events:
        if e.get("chat_id") == chat_id and e.get("week_start") == week_start:
            return
    events.append({"chat_id": chat_id, "week_start": week_start, "sent_at": "CURRENT_TIMESTAMP"})
    _save_db(data)

def has_sent_weekly_report(chat_id, week_start):
    data = _load_db()
    reports = data.get("weekly_report_sends", [])
    for r in reports:
        if r.get("chat_id") == chat_id and r.get("week_start") == week_start:
            return True
    return False

def record_weekly_report_send(chat_id, week_start):
    data = _load_db()
    reports = data.setdefault("weekly_report_sends", [])
    for r in reports:
        if r.get("chat_id") == chat_id and r.get("week_start") == week_start:
            return False
    reports.append({"chat_id": chat_id, "week_start": week_start, "sent_at": "CURRENT_TIMESTAMP"})
    _save_db(data)
    return True

# Chat topic settings
def set_chat_topic(chat_id, message_thread_id):
    data = _load_db()
    topics = data.setdefault("chat_topics", [])
    updated = False
    for t in topics:
        if t.get("chat_id") == chat_id:
            t["message_thread_id"] = message_thread_id
            t["updated_at"] = "CURRENT_TIMESTAMP"
            updated = True
            break
    if not updated:
        topics.append({"chat_id": chat_id, "message_thread_id": message_thread_id, "updated_at": "CURRENT_TIMESTAMP"})
    _save_db(data)

def clear_chat_topic(chat_id):
    data = _load_db()
    topics = data.get("chat_topics", [])
    new_topics = [t for t in topics if t.get("chat_id") != chat_id]
    if len(new_topics) < len(topics):
        data["chat_topics"] = new_topics
        _save_db(data)

def get_chat_topic(chat_id):
    data = _load_db()
    topics = data.get("chat_topics", [])
    for t in topics:
        if t.get("chat_id") == chat_id:
            return t.get("message_thread_id")
    return None

# Alert Level settings
ALERT_LEVELS = ("OFF", "MARKET", "IMPORTANT", "ALL")

ALERT_LEVEL_LABELS = {
    "OFF": "🔕 모든 알람 받지 않음",
    "MARKET": "🌍 시장 알림만 (장마감 요약 등)",
    "IMPORTANT": "⭐ 중요 알림 + 시장 알림",
    "ALL": "🔔 모든 알람 받기",
}

def set_chat_alert_level(chat_id, level):
    if level not in ALERT_LEVELS:
        raise ValueError(f"Invalid alert level: {level}")
    data = _load_db()
    settings = data.setdefault("chat_alert_settings", [])
    
    enabled = 0 if level == "OFF" else 1
    updated = False
    for s in settings:
        if s.get("chat_id") == chat_id:
            s["alerts_enabled"] = enabled
            s["alert_level"] = level
            s["updated_at"] = "CURRENT_TIMESTAMP"
            updated = True
            break
    if not updated:
        settings.append({
            "chat_id": chat_id,
            "alerts_enabled": enabled,
            "alert_level": level,
            "updated_at": "CURRENT_TIMESTAMP"
        })
    _save_db(data)
    return True

def get_chat_alert_level(chat_id):
    data = _load_db()
    settings = data.get("chat_alert_settings", [])
    for s in settings:
        if s.get("chat_id") == chat_id:
            level = s.get("alert_level")
            if level in ALERT_LEVELS:
                return level
            return "ALL" if s.get("alerts_enabled", 1) else "OFF"
    return "ALL"

def should_send_alert(chat_id, is_market_wide=False, is_important=False):
    level = get_chat_alert_level(chat_id)
    if level == "OFF":
        return False
    if level == "MARKET":
        return is_market_wide
    if level == "IMPORTANT":
        return is_market_wide or is_important
    return True

def set_chat_alerts_enabled(chat_id, enabled):
    current = get_chat_alert_level(chat_id)
    if enabled and current == "OFF":
        return set_chat_alert_level(chat_id, "ALL")
    if not enabled:
        return set_chat_alert_level(chat_id, "OFF")
    return True

def get_chat_alerts_enabled(chat_id):
    return get_chat_alert_level(chat_id) != "OFF"

# Signal alerts tracking
def has_sent_signal_alert(chat_id, ticker, signal_type, alert_date):
    ticker = ticker.upper().strip()
    data = _load_db()
    alerts = data.get("daily_signal_alerts", [])
    for a in alerts:
        if (a.get("chat_id") == chat_id and a.get("ticker") == ticker and 
            a.get("signal_type") == signal_type and a.get("alert_date") == alert_date):
            return True
    return False

def record_signal_alert(chat_id, ticker, signal_type, alert_date, price=None):
    ticker = ticker.upper().strip()
    data = _load_db()
    alerts = data.setdefault("daily_signal_alerts", [])
    for a in alerts:
        if (a.get("chat_id") == chat_id and a.get("ticker") == ticker and 
            a.get("signal_type") == signal_type and a.get("alert_date") == alert_date):
            return False
    alerts.append({
        "chat_id": chat_id,
        "ticker": ticker,
        "signal_type": signal_type,
        "alert_date": alert_date,
        "price": price,
        "created_at": "CURRENT_TIMESTAMP"
    })
    _save_db(data)
    return True

# Recommendation alerts tracking
def has_sent_recommendation_alert(chat_id, ticker, alert_date, alert_type):
    ticker = ticker.upper().strip()
    data = _load_db()
    alerts = data.get("recommendation_alerts", [])
    for a in alerts:
        if (a.get("chat_id") == chat_id and a.get("ticker") == ticker and 
            a.get("alert_date") == alert_date and a.get("alert_type") == alert_type):
            return True
    return False

def record_recommendation_alert(chat_id, ticker, alert_date, alert_type, price):
    ticker = ticker.upper().strip()
    data = _load_db()
    alerts = data.setdefault("recommendation_alerts", [])
    for a in alerts:
        if (a.get("chat_id") == chat_id and a.get("ticker") == ticker and 
            a.get("alert_date") == alert_date and a.get("alert_type") == alert_type):
            return False
    alerts.append({
        "chat_id": chat_id,
        "ticker": ticker,
        "alert_date": alert_date,
        "alert_type": alert_type,
        "price": price,
        "created_at": "CURRENT_TIMESTAMP"
    })
    _save_db(data)
    return True
