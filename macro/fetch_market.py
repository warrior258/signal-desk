import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
import yfinance as yf

root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

import db

# Macro configurations: thresholds, rate-of-change, and sanity ranges
MACRO_CONFIG = {
    "brent": {
        "symbol": "BZ=F",
        "name": "Brent Crude Oil ($/bbl)",
        "area": "Fuel",
        "valid_range": (35.0, 200.0),
        "amber_abs": 90.0,
        "red_abs": 105.0,
        "amber_chg": 10.0,
        "red_chg": 20.0,
    },
    "usdinr": {
        "symbol": "INR=X",
        "name": "USD / INR Exchange Rate",
        "area": "Finance",
        "valid_range": (65.0, 150.0),
        "amber_abs": 98.0,
        "red_abs": 102.0,
        "amber_chg": 2.5,
        "red_chg": 5.0,
    },
    "vix": {
        "symbol": "^INDIAVIX",
        "name": "India VIX (Volatility)",
        "area": "Finance",
        "valid_range": (8.0, 90.0),
        "amber_abs": 18.0,
        "red_abs": 24.0,
    },
    "nifty": {
        "symbol": "^NSEI",
        "name": "Nifty 50 Index",
        "area": "Finance",
        "valid_range": (12000.0, 45000.0),
        "amber_dd": -5.0,
        "red_dd": -10.0,
    }
}


def evaluate_status(key: str, val: float, change_30d: float, cfg: dict) -> str:
    """
    Evaluates status (green, amber, red) using both absolute levels and rate-of-change.
    Avoids flagging benign values (e.g. low VIX with high % move).
    """
    if key == "vix":
        # Normal/calm: VIX < 18.0 is always GREEN, regardless of % change
        if val < cfg["amber_abs"]:
            return "green"
        if val >= cfg["red_abs"]:
            return "red"
        return "amber"

    if key == "brent":
        if val >= cfg["red_abs"] or (val >= cfg["amber_abs"] and change_30d >= cfg["red_chg"]):
            return "red"
        if val >= cfg["amber_abs"] or change_30d >= cfg["amber_chg"]:
            return "amber"
        return "green"

    if key == "usdinr":
        if val >= cfg["red_abs"] or change_30d >= cfg["red_chg"]:
            return "red"
        if val >= cfg["amber_abs"] or change_30d >= cfg["amber_chg"]:
            return "amber"
        return "green"

    if key == "nifty":
        if change_30d <= cfg["red_dd"]:
            return "red"
        if change_30d <= cfg["amber_dd"]:
            return "amber"
        return "green"

    return "green"


def fetch_macro_indicators() -> list[dict]:
    """
    Fetches latest prices and 30-day changes for core macro market indicators.
    Includes data sanity checks: plausibility, staleness, and non-empty history.
    """
    results = []
    now_utc = datetime.now(timezone.utc)
    now_iso = now_utc.isoformat()

    for key, cfg in MACRO_CONFIG.items():
        sym = cfg["symbol"]
        try:
            t = yf.Ticker(sym)
            hist = t.history(period="1mo")
            if hist.empty or len(hist) < 1:
                print(f"[WARN] No market history returned for {key} ({sym}).")
                continue

            last_val = float(hist["Close"].iloc[-1])
            first_val = float(hist["Close"].iloc[0])
            pct_change_30d = ((last_val - first_val) / first_val) * 100.0 if first_val > 0 else 0.0

            # 1. Sanity check: Plausibility range
            min_valid, max_valid = cfg["valid_range"]
            if not (min_valid <= last_val <= max_valid):
                print(f"[WARN] Implausible value for {key} ({sym}): {last_val} (expected {min_valid}-{max_valid}). Skipping.")
                continue

            # 2. Sanity check: Staleness (older than 3 days, adjusting for weekends)
            last_date = hist.index[-1]
            if hasattr(last_date, "to_pydatetime"):
                last_dt = last_date.to_pydatetime()
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=timezone.utc)
                age_days = (now_utc - last_dt).total_seconds() / 86400.0
                if age_days > 4.0:
                    print(f"[WARN] Stale data for {key} ({sym}): last update was {age_days:.1f} days ago.")

            status = evaluate_status(key, last_val, pct_change_30d, cfg)

            item = {
                "key": key,
                "name": cfg["name"],
                "area": cfg["area"],
                "symbol": sym,
                "value": round(last_val, 2),
                "change_30d_pct": round(pct_change_30d, 2),
                "status": status,
                "first_seen_at": now_iso
            }
            results.append(item)

            # Persist to database
            save_indicator_to_db(item)

        except Exception as e:
            print(f"[WARN] Error fetching macro indicator {key} ({sym}): {e}")

    return results


def save_indicator_to_db(item: dict):
    """Saves indicator definition and current reading to SQLite."""
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
        INSERT OR IGNORE INTO indicators (name, area, source, frequency, amber_threshold, red_threshold, higher_is_worse)
        VALUES (?, ?, 'yfinance', 'daily', 0, 0, 1);
        """, (item["key"], item["area"]))

        cursor.execute("SELECT id FROM indicators WHERE name = ?;", (item["key"],))
        ind_row = cursor.fetchone()
        if ind_row:
            cursor.execute("""
            INSERT INTO indicator_values (indicator_id, value, status, observed_for, first_seen_at)
            VALUES (?, ?, ?, ?, ?);
            """, (
                ind_row["id"],
                item["value"],
                item["status"],
                item["first_seen_at"][:10],
                item["first_seen_at"]
            ))
        conn.commit()


if __name__ == "__main__":
    db.init_db()
    print("Testing macro fetch with sanity checks...")
    items = fetch_macro_indicators()
    for it in items:
        icon = "🔴" if it["status"] == "red" else "🟠" if it["status"] == "amber" else "🟢"
        print(f"{icon} [{it['area']}] {it['name']}: {it['value']} ({it['change_30d_pct']:+.1f}% 30d) -> {it['status'].upper()}")
