import os

def load_dotenv(filepath=".env"):
    """Custom .env file parser. 이미 설정된 환경변수(Render 등)는 덮어쓰지 않습니다."""
    if not os.path.exists(filepath):
        return

    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            if "=" in line:
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip()

                # Remove quotes if wrapped
                if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                    val = val[1:-1]

                # Render 환경변수가 우선, .env는 로컬 개발용
                os.environ.setdefault(key, val)

load_dotenv()

# System Configs
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", 3600))  # Default: 1 hour
PRICE_CHECK_INTERVAL = max(10, int(os.environ.get("PRICE_CHECK_INTERVAL", 60)))
DB_PATH = os.environ.get("DB_PATH", "data/stock_bot.db")

# JSONbin.io Cloud Storage Configs
JSONBIN_API_KEY = os.environ.get("JSONBIN_API_KEY", "").strip()
JSONBIN_BIN_ID = os.environ.get("JSONBIN_BIN_ID", "").strip()

# Ensure data directory exists
db_dir = os.path.dirname(DB_PATH)
if db_dir and not os.path.exists(db_dir):
    os.makedirs(db_dir, exist_ok=True)

# Toss Securities Open API (optional)
TOSS_CLIENT_ID = os.environ.get("TOSS_CLIENT_ID", "").strip()
TOSS_CLIENT_SECRET = os.environ.get("TOSS_CLIENT_SECRET", "").strip()
