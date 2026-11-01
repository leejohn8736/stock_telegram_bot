import time
import sys
import os
import database
from config import TELEGRAM_BOT_TOKEN, DB_PATH, CHECK_INTERVAL
from telegram_bot import TelegramBot
from scheduler import AlertScheduler
import toss_api

def check_environment():
    """Validates essential configuration before launching."""
    if not TELEGRAM_BOT_TOKEN:
        print("\n" + "="*60)
        print("❌ ERROR: TELEGRAM_BOT_TOKEN IS NOT CONFIGURED!")
        print("="*60)
        print("텔레그램 봇 토큰이 설정되지 않았습니다.")
        print("동일한 폴더에 '.env' 파일을 생성하고 아래와 같이 토큰을 입력해 주세요:")
        print("\nTELEGRAM_BOT_TOKEN=123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ")
        print(f"CHECK_INTERVAL={CHECK_INTERVAL}")
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
        print("   - 더 빠른 조회를 원하면 .env에 TOSS_CLIENT_ID / TOSS_CLIENT_SECRET을 설정하세요.")
        
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
    
    # 💡 GitHub Actions 환경(CI=true)인지 확인하여 자동 종료 처리
    is_github_actions = os.environ.get("CI") == "true"

    if is_github_actions:
        print("🤖 GitHub Actions 환경 감지: 스캔 및 알림 전송 후 안전하게 종료합니다.")
        time.sleep(3.0)  # 초기 스캔 및 메시지 발송 대기 시간
        scheduler.stop()
        bot.stop_polling()
        time.sleep(1.5)
        print("👋 GitHub Actions에서 서비스가 성공적으로 종료되었습니다.")
        sys.exit(0)

    # 로컬 PC에서 실행할 때만 24시간 대기 유지
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
