import csv
import re
import sys
from pathlib import Path
import requests
from rapidfuzz import fuzz, process

root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from config import EQUITY_CSV_PATH

NSE_EQUITY_URL = "https://archives.nseindia.com/content/equities/EQUITY_L.csv"

_CACHE_LOADED = False
_NAMES = []
_SYMBOLS = []
_CLEANED_NAMES = []
_EXACT_MAP = {}

def ensure_equity_file():
    """Downloads EQUITY_L.csv from NSE if missing."""
    if not EQUITY_CSV_PATH.exists():
        print(f"[INFO] Downloading NSE Equity list to {EQUITY_CSV_PATH}...")
        try:
            r = requests.get(NSE_EQUITY_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
            r.raise_for_status()
            EQUITY_CSV_PATH.parent.mkdir(exist_ok=True)
            with open(EQUITY_CSV_PATH, "w", encoding="utf-8") as f:
                f.write(r.text)
            print("[INFO] NSE Equity list downloaded successfully.")
        except Exception as e:
            print(f"[WARN] Failed to download EQUITY_L.csv from NSE: {e}")

def _clean_name(name: str) -> str:
    """Normalizes company name for comparison."""
    n = name.lower()
    n = re.sub(r"\b(limited|ltd\.?|pvt\.?|private|industries|india|corp\.?|corporation)\b", "", n)
    n = re.sub(r"[^\w\s]", "", n)
    return n.strip()

def load_stocks():
    """Loads stocks into memory for fast fuzzy matching."""
    global _CACHE_LOADED, _NAMES, _SYMBOLS, _CLEANED_NAMES, _EXACT_MAP
    if _CACHE_LOADED:
        return

    ensure_equity_file()
    if not EQUITY_CSV_PATH.exists():
        print(f"[WARN] {EQUITY_CSV_PATH} not found. Stock matching disabled.")
        return

    names, symbols, cleaned = [], [], []
    exact_map = {}

    with open(EQUITY_CSV_PATH, mode="r", encoding="utf-8", errors="ignore") as f:
        reader = csv.DictReader(f)
        for row in reader:
            sym = row.get("SYMBOL", "").strip()
            name = row.get("NAME OF COMPANY", "").strip()
            if sym and name:
                names.append(name)
                symbols.append(sym)
                c_name = _clean_name(name)
                cleaned.append(c_name)
                exact_map[sym.upper()] = sym
                exact_map[name.lower()] = sym
                if c_name:
                    exact_map[c_name] = sym

    _NAMES = names
    _SYMBOLS = symbols
    _CLEANED_NAMES = cleaned
    _EXACT_MAP = exact_map
    _CACHE_LOADED = True

def match(company_name: str, min_score: int = 82) -> str | None:
    """
    Resolves a company name to an NSE trading symbol.
    Uses exact lookup first, followed by rapidfuzz fuzzy matching.
    """
    if not company_name:
        return None
    load_stocks()
    if not _NAMES:
        return None

    cleaned = _clean_name(company_name)

    # 1. Exact match checks
    if company_name.upper() in _EXACT_MAP:
        return _EXACT_MAP[company_name.upper()]
    if company_name.lower() in _EXACT_MAP:
        return _EXACT_MAP[company_name.lower()]
    if cleaned in _EXACT_MAP:
        return _EXACT_MAP[cleaned]

    # 2. Fuzzy match against cleaned names
    match_result = process.extractOne(
        cleaned,
        _CLEANED_NAMES,
        scorer=fuzz.token_sort_ratio,
        score_cutoff=min_score
    )

    if match_result:
        matched_str, score, idx = match_result
        return _SYMBOLS[idx]

    # 3. Fallback fuzzy match against full original names
    fallback_result = process.extractOne(
        company_name,
        _NAMES,
        scorer=fuzz.WRatio,
        score_cutoff=min_score
    )
    if fallback_result:
        matched_str, score, idx = fallback_result
        return _SYMBOLS[idx]

    return None

if __name__ == "__main__":
    ensure_equity_file()
    load_stocks()
    print(f"Loaded {len(_SYMBOLS)} NSE stocks.")

    test_queries = [
        "Tata Motors Limited",
        "Infosys",
        "Reliance Industries",
        "Praj Industries",
        "Bharat Electronics",
        "Random Unlisted Corp",
    ]
    for q in test_queries:
        sym = match(q)
        print(f"'{q}' -> Symbol: {sym}")
