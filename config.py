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

# ---------------------------------------------------------------------------
# Freshness controls
#
# Google News RSS ranks by relevance, NOT by date: an unfiltered query happily
# returns articles from years ago, and even the "when:" operator leaks older
# items through. So "when:" is only a hint to improve the result mix -- the
# authoritative cut is MAX_DOC_AGE_HOURS, applied locally in collectors/feeds.py.
# ---------------------------------------------------------------------------
FEED_WINDOW = "when:2d"      # hint passed to Google News
PIB_MAX_RELEASES = 25        # release pages inspected per cycle from the PIB listing
PIB_FETCH_DELAY_SECONDS = 0.4    # politeness delay between PIB page fetches
MIN_EVIDENCE_CHARS = 400     # below this, a document is a headline, not evidence
MAX_DOC_AGE_HOURS = 36       # hard cutoff: anything published before this is dropped
BACKLOG_MAX_AGE_HOURS = 48   # how far back the unanalyzed-document queue may reach
MARKET_CACHE_TTL_SECONDS = 900   # reuse a symbol's quote for 15 min within a run
MARKET_MAX_STALE_DAYS = 4.0  # price history older than this is not trusted for scoring

# Gemini models, tried in order until one returns valid JSON.
# Verified live against this project's key. Avoid the "-latest" aliases here:
# they resolve to whichever model is currently promoted and have been returning
# 503s, so the fallback chain would start with the least reliable option.
GEMINI_MODELS = [m.strip() for m in os.getenv(
    "GEMINI_MODELS", "gemini-3.8-flash,gemini-3.5-flash,gemini-3.5-flash-lite"
).split(",") if m.strip()]

# Pre-Filter Action Keywords
# Matched on word boundaries (see processing/filter.py) so that "ban" does not
# match "banking", "bill" does not match "billion", "cess" does not match "process".
ACTION_WORDS = [
    "draft", "notification", "mandatory", "ban", "prohibit", "certification",
    "quality control order", "qco", "amendment", "bill", "ordinance", "duty",
    "subsidy", "incentive", "pli", "blending", "consultation", "exposure draft",
    "compliance", "levy", "cess", "tariff", "anti-dumping", "safeguard duty",
]
