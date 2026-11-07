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

# 글로벌 봇 인스턴스 (웹훅 핸들러에서 메시지 처리를 위해 필요)
bot_instance = None

# -------------------------------------------------------------
# 🌐 Render 웹 서비스 포트 바인딩 및 텔레그램 웹훅(Webhook) 수신 서버
# -------------------------------------------------------------
class WebhookHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        """텔레그램 서버가 보내주는 메시지 이벤트를 수신하는 엔드포인트"""
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length)
            
            if bot_instance:
                # 텔레그램 업데이트 데이터를 봇 객체로 전달하여 처리
                bot_instance.process_webhook_data(post_data)
                
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")
        except Exception as e:
            print(f"⚠️ [Webhook Error] {e}")
            self.send_response(500)
            self.end_headers()

    def do_GET(self):
        """Render 헬스체크 및 자체 셀프 핑 응답용"""
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Telegram Stock Alert Bot is running via Webhook!")
    
    def log_message(self, format, *args):
        # 불필요한 접속 로그 도배 방지
        return

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), WebhookHandler)
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
            time.sleep(600)  # 10분마다 핑
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
    global bot_instance
    print("🚀 Initializing Telegram Stock Alert Bot (Webhook Mode)...")
    
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
    
    # 텔레그램 봇 설정 및 웹훅 등록
    print("🤖 Starting Telegram Webhook Service...")
    bot_instance = TelegramBot()
    bot_instance.set_my_commands()
    
    # Render 외부 URL에 웹훅 등록
    render_url = os.environ.get("RENDER_EXTERNAL_URL", "https://stock-telegram-bot-35i4.onrender.com")
    webhook_url = f"{render_url}/{TELEGRAM_BOT_TOKEN}"
    bot_instance.set_webhook(webhook_url)
    print(f"🔗 Webhook registered to: {webhook_url}")
    
    # 백그라운드 스케줄러 시작
    print("⏰ Starting Technical Indicator Background Scan Service...")
    scheduler = AlertScheduler(bot_instance)
    scheduler.start()
    
    print("\n" + "="*50)
    print("🎉 STOCK ALERT BOT IS NOW RUNNING VIA WEBHOOK!")
    print(f"- SQLite DB: {DB_PATH}")
    print(f"- Scan Interval: {CHECK_INTERVAL} seconds")
    print("="*50 + "\n")
    
    # 1. 웹 서버(웹훅 수신 + 포트 바인딩 + 헬스체크) 스레드 구동
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
        bot_instance.remove_webhook()
        sys.exit(0)

if __name__ == "__main__":
    main()
