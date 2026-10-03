import os
from pathlib import Path
from dotenv import load_dotenv

# Base Directory
BASE_DIR = Path(__file__).resolve().parent

# Load environment variables from .env
load_dotenv(BASE_DIR / ".env")

# API Keys & Secrets
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

# Paths
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
DB_PATH = DATA_DIR / "bot.db"
EQUITY_CSV_PATH = DATA_DIR / "EQUITY_L.csv"

# Pre-Filter Action Keywords
ACTION_WORDS = [
    "draft", "notification", "mandatory", "ban", "prohibit", "certification",
    "quality control order", "qco", "amendment", "bill", "ordinance", "duty",
    "subsidy", "incentive", "pli", "blending", "consultation", "exposure draft",
    "compliance", "levy", "cess", "tariff", "anti-dumping", "safeguard duty",
]

# Macro Thresholds (Starter values)
MACRO_THRESHOLDS = {
    "brent": {"amber": 85.0, "red": 95.0, "higher_is_worse": True},       # USD / barrel
    "usdinr": {"amber": 84.5, "red": 86.0, "higher_is_worse": True},      # INR per USD
    "vix": {"amber": 18.0, "red": 24.0, "higher_is_worse": True},          # India VIX
    "nifty_dd": {"amber": -5.0, "red": -10.0, "higher_is_worse": False},   # % drawdown from 30d high
}
