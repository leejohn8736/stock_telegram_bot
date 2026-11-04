import time
import sys
import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import database
from config import TELEGRAM_BOT_TOKEN, DB_PATH, CHECK_INTERVAL
from telegram_bot import TelegramBot
from scheduler import AlertScheduler
import toss_api

# -------------------------------------------------------------
# 🌐 Render 웹 서비스 포트 타임아웃 방지용 가벼운 웹 서버 (백그라운드 실행)
# -------------------------------------------------------------
class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Telegram Stock Alert Bot is running!")
    
    def log_message(self, format, *args):
        # 불필요한 HTTP 접속 로그가 터미널을 도배하지 않도록 차단
        return

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), SimpleHandler)
    server.serve_forever()

# -------------------------------------------------------------
# 🚀 봇 메인 로직
# -------------------------------------------------------------
def check_environment():
    """Validates essential configuration before launching."""
    if not TELEGRAM_BOT_TOKEN:
        print("\n" + "="*60)
        print("❌ ERROR: TELEGRAM_BOT_TOKEN IS NOT CONFIGURED!")
        print("="*60)
        print("텔레그램 봇 토큰이 설정되지 않았습니다.")
        print("Render 환경 변수(Environment Variables)에 TELEGRAM_BOT_TOKEN을 등록해 주세요.")
        print("="*60 + "\n")
        return False
    return True

def main():
    print("🚀 Initializing Telegram Stock Alert Bot...")
    
    if not check_environment():
        sys.exit(1)

    # Display data source status
    toss_status = toss_api.check_connection()
    if toss_status["configured"]:
        if toss_status["ok"]:
            print("✅ Data Source: 토스증권 Open API 사용 (TOSS_CLIENT_ID / TOSS_CLIENT_SECRET 설정됨)")
            print(f"   - {toss_status['message']}")
            print("   - 실패 시 기존 Yahoo Finance 방식으로 자동 폴백합니다.")
        else:
            print("⚠️ Data Source: 토스증권 Open API 설정됨 BUT 연결 실패")
            print(f"   - 사유: {toss_status['message']}")
            print("   - 기존 Yahoo Finance 방식으로 동작합니다.")
    else:
        print("ℹ️  Data Source: Yahoo Finance 사용")
        
    # Initialize components
    print(f"📂 Initializing database at '{DB_PATH}'...")
    database.init_db()
    print("✅ Database initialized successfully.")
    
    print("🤖 Starting Telegram Polling Service...")
    bot = TelegramBot()
    bot.set_my_commands()
    bot.start_polling()
    
    print("⏰ Starting Technical Indicator Background Scan Service...")
    scheduler = AlertScheduler(bot)
    scheduler.start()
    
    print("\n" + "="*50)
    print("🎉 STOCK ALERT BOT IS NOW RUNNING SUCCESSFULLY!")
    print(f"- SQLite DB: {DB_PATH}")
    print(f"- Scan Interval: {CHECK_INTERVAL} seconds ({CHECK_INTERVAL/60:.1f} minutes)")
    if toss_status["configured"] and toss_status["ok"]:
        print("- Data Source: 토스증권 Open API ⚡ (빠른 모드)")
    else:
        print("- Data Source: Yahoo Finance")
    print("Press CTRL+C to terminate services gracefully.")
    print("="*50 + "\n")
    
    # Render 포트 검사를 통과하기 위해 백그라운드 웹 서버 스레드 구동
    threading.Thread(target=run_web_server, daemon=True).start()

    # Main loop with graceful shutdown (24/7 상시 구동 무한 루프)
    try:
        while True:
            time.sleep(1)
    except (KeyboardInterrupt, SystemExit):
        print("\n⚠️ Termination signal received. Shutting down gracefully...")
        scheduler.stop()
        bot.stop_polling()
        time.sleep(1.5)
        print("👋 Services successfully terminated. Goodbye!")
        sys.exit(0)

if __name__ == "__main__":
    main()
