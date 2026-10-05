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

# Non-corporate entities the LLM often names. They are not tradeable, and fuzzy
# matching used to map them onto unrelated tickers (e.g. "Food Corporation of
# India" -> 3MINDIA), inventing signals out of nothing.
_ENTITY_BLOCKLIST = re.compile(
    r"\b(ministry|ministries|government|govt|cabinet|authority|department|"
    r"commission|council|bureau|federation|association|parliament|"
    r"secretariat|directorate|tribunal|regulator|niti aayog|"
    r"rbi|sebi|trai|irdai|dgft|meity|morth|bis|fssai|cbic|cbdt)\b",
    re.IGNORECASE,
)

# Dropped when normalising a name, so "State Bank of India" and "State Bank" agree.
_NOISE_WORDS = re.compile(
    r"\b(limited|ltd|pvt|private|industries|india|indian|corp|corporation|"
    r"company|co|the|of|and)\b",
    re.IGNORECASE,
)

MIN_CLEAN_LENGTH = 4   # below this a cleaned name carries too little signal
FUZZY_CUTOFF = 88      # cleaned-name match
STRICT_CUTOFF = 93     # token-set fallback, deliberately strict

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
    """Normalizes a company name for comparison."""
    n = _NOISE_WORDS.sub(" ", name.lower())
    n = re.sub(r"[^\w\s]", " ", n)
    return re.sub(r"\s+", " ", n).strip()


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
            if not sym or not name:
                continue
            names.append(name)
            symbols.append(sym)
            c_name = _clean_name(name)
            cleaned.append(c_name)
            exact_map[sym.upper()] = sym
            exact_map[name.lower()] = sym
            # Don't let a short/ambiguous cleaned name claim an exact slot.
            if len(c_name) >= MIN_CLEAN_LENGTH:
                exact_map.setdefault(c_name, sym)

    _NAMES = names
    _SYMBOLS = symbols
    _CLEANED_NAMES = cleaned
    _EXACT_MAP = exact_map
    _CACHE_LOADED = True


def match(company_name: str, min_score: int = FUZZY_CUTOFF) -> str | None:
    """
    Resolves a company name to an NSE trading symbol.
    Returns None rather than a doubtful guess: a wrong symbol is worse than no signal.
    """
    if not company_name:
        return None

    name = company_name.strip()
    if _ENTITY_BLOCKLIST.search(name):
        return None

    load_stocks()
    if not _NAMES:
        return None

    # 1. Exact lookups (symbol, full name, normalised name)
    if name.upper() in _EXACT_MAP:
        return _EXACT_MAP[name.upper()]
    if name.lower() in _EXACT_MAP:
        return _EXACT_MAP[name.lower()]

    cleaned = _clean_name(name)
    if len(cleaned) < MIN_CLEAN_LENGTH:
        return None
    if cleaned in _EXACT_MAP:
        return _EXACT_MAP[cleaned]

    # 2. Fuzzy match on normalised names
    hit = process.extractOne(
        cleaned, _CLEANED_NAMES, scorer=fuzz.token_sort_ratio, score_cutoff=min_score
    )
    if hit:
        return _SYMBOLS[hit[2]]

    # 3. Strict token-set fallback for word-order and extra-token differences.
    #    token_set_ratio, not WRatio: WRatio's partial matching let a shared
    #    token like "India" pull unrelated companies over the line.
    #    It scores a pure subset at 100 ("food" vs "bectors food specialities"),
    #    so require >=2 tokens and a comparable candidate length.
    q_tokens = cleaned.split()
    if len(q_tokens) >= 2:
        for cand, _score, idx in process.extract(
            cleaned, _CLEANED_NAMES, scorer=fuzz.token_set_ratio,
            limit=5, score_cutoff=STRICT_CUTOFF
        ):
            if abs(len(cand.split()) - len(q_tokens)) <= 1:
                return _SYMBOLS[idx]

    return None


if __name__ == "__main__":
    ensure_equity_file()
    load_stocks()
    print(f"Loaded {len(_SYMBOLS)} NSE stocks.\n")

    # (query, expected symbol or None)
    cases = [
        ("Tata Motors Limited", "TMCV"),
        ("Infosys", "INFY"),
        ("Reliance Industries", "RELIANCE"),
        ("Praj Industries", "PRAJIND"),
        ("Bharat Electronics", "BEL"),
        ("NTPC", "NTPC"),
        ("State Bank of India", "SBIN"),
        ("Life Insurance Corporation of India", "LICI"),
        # Must NOT match: unlisted bodies and junk
        ("Random Unlisted Corp", None),
        ("Government of India", None),
        ("Ministry of Steel", None),
        ("National Highways Authority of India", None),
        ("Food Corporation of India", None),
    ]
    failures = 0
    for q, expected in cases:
        got = match(q)
        ok = got == expected
        failures += not ok
        print(f"[{'ok  ' if ok else 'FAIL'}] {q:38} -> {got}  (expected {expected})")
    print("\nAll matcher tests passed." if not failures else f"\n{failures} matcher test(s) failed.")
