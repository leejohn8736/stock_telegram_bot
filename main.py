import time
import sys
import os
import threading
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler
import database
from config import TELEGRAM_BOT_TOKEN, DB_PATH, CHECK_INTERVAL
from telegram_bot import TelegramBot
from scheduler import AlertScheduler
import toss_api

# -------------------------------------------------------------
# 🌐 Render 웹 서비스 포트 타임아웃 방지용 가벼운 웹 서버
# -------------------------------------------------------------
class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Telegram Stock Alert Bot is running!")
     
    def log_message(self, format, *args):
        return

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), SimpleHandler)
    server.serve_forever()

# -------------------------------------------------------------
# 🔄 Render 절전 모드 방지용 셀프 핑(Self-Ping) 백그라운드 함수
# -------------------------------------------------------------
def self_ping_loop():
    app_url = os.environ.get("RENDER_EXTERNAL_URL")
    if not app_url:
        app_url = "https://stock-telegram-bot-35i4.onrender.com"
        
    print(f"🔄 [Self-Ping] Self-ping service started. Target URL: {app_url}")
    
    while True:
        try:
            time.sleep(600)
            response = requests.get(app_url, timeout=10)
            print(f"ping... self-ping sent (Status: {response.status_code})")
        except Exception as e:
            print(f"⚠️ [Self-Ping] Error during self-ping: {e}")

# -------------------------------------------------------------
# 🚀 봇 메인 로직
# -------------------------------------------------------------
def check_environment():
    if not TELEGRAM_BOT_TOKEN:
        print("\n" + "="*60)
        print("❌ ERROR: TELEGRAM_BOT_TOKEN IS NOT CONFIGURED!")
        print("="*60 + "\n")
        return False
    return True

def main():
    print("🚀 Initializing Telegram Stock Alert Bot...")
    
    if not check_environment():
        sys.exit(1)

    # 데이터 소스 상태 확인
    toss_status = toss_api.check_connection()
    if toss_status["configured"]:
        if toss_status["ok"]:
            print("✅ Data Source: 토스증권 Open API 사용")
        else:
            print("⚠️ Data Source: 토스증권 Open API 연결 실패 -> Yahoo Finance 폴백")
    else:
        print("ℹ️ Data Source: Yahoo Finance 사용")
        
    # 데이터베이스 초기화
    print(f"📂 Initializing database at '{DB_PATH}'...")
    database.init_db()
    print("✅ Database initialized successfully.")
    
    # 텔레그램 봇 폴링 시작 (안전한 기존 방식)
    print("🤖 Starting Telegram Polling Service...")
    bot = TelegramBot()
    try:
        bot.set_my_commands()
    except Exception as e:
        print(f"⚠️ Warning during set_my_commands (ignoring): {e}")
        
    bot.start_polling()
    
    print("⏰ Starting Technical Indicator Background Scan Service...")
    scheduler = AlertScheduler(bot)
    scheduler.start()
    
    print("\n" + "="*50)
    print("🎉 STOCK ALERT BOT IS NOW RUNNING SUCCESSFULLY!")
    print(f"- SQLite DB: {DB_PATH}")
    print(f"- Scan Interval: {CHECK_INTERVAL} seconds")
    print("="*50 + "\n")
    
    # 1. Render 포트 바인딩용 웹 서버 스레드 구동
    threading.Thread(target=run_web_server, daemon=True).start()
    
    # 2. 슬립 방지 셀프 핑 스레드 구동
    threading.Thread(target=self_ping_loop, daemon=True).start()

    # 상시 구동 무한 루프
    try:
        while True:
            time.sleep(1)
    except (KeyboardInterrupt, SystemExit):
        print("\n⚠️ Termination signal received. Shutting down...")
        scheduler.stop()
        bot.stop_polling()
        sys.exit(0)

if __name__ == "__main__":
    main()
