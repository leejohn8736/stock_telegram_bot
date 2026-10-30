import os

def load_dotenv(filepath=".env"):
    """Custom .env file parser to load environment variables without external packages."""
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
                
                os.environ[key] = val

load_dotenv()

# System Configs
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", 3600))  # Default: 1 hour
PRICE_CHECK_INTERVAL = max(10, int(os.environ.get("PRICE_CHECK_INTERVAL", 60)))
DB_PATH = os.environ.get("DB_PATH", "data/stock_bot.db")

# Ensure data directory exists
db_dir = os.path.dirname(DB_PATH)
if db_dir and not os.path.exists(db_dir):
    os.makedirs(db_dir, exist_ok=True)

# Toss Securities Open API (optional)
TOSS_CLIENT_ID = os.environ.get("TOSS_CLIENT_ID", "")
TOSS_CLIENT_SECRET = os.environ.get("TOSS_CLIENT_SECRET", "")
