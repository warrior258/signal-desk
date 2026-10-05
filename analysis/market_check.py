import sys
import time
from datetime import datetime, timezone
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

from config import MARKET_CACHE_TTL_SECONDS, MARKET_MAX_STALE_DAYS

# A cycle often names the same symbol across several documents; one quote per
# symbol per TTL keeps us off yfinance's rate limiter.
_CACHE: dict[str, tuple[float, dict]] = {}


def _empty(symbol: str) -> dict:
    return {
        "symbol": symbol,
        "price": 0.0,
        "change_today_pct": 0.0,
        "change_5d_pct": 0.0,
        "volume_ratio": 1.0,
        "as_of": None,
        "stale": True,
        "available": False,
    }


def check_stock_market(symbol: str, use_cache: bool = True) -> dict:
    """
    Fetches latest price, short-term momentum, and volume ratio for an NSE stock.
    Appends .NS for Indian equity on Yahoo Finance.

    `stale` marks data whose last bar is older than MARKET_MAX_STALE_DAYS, so
    scoring can ignore price action that no longer reflects the market.
    """
    key = symbol.strip().upper()
    if not key:
        return _empty(symbol)

    if use_cache:
        cached = _CACHE.get(key)
        if cached and (time.time() - cached[0]) < MARKET_CACHE_TTL_SECONDS:
            return cached[1]

    try:
        hist = yf.Ticker(f"{key}.NS").history(period="1mo", interval="1d", auto_adjust=False)
        if hist.empty or len(hist) < 2:
            result = _empty(symbol)
        else:
            last_close = float(hist["Close"].iloc[-1])
            prev_close = float(hist["Close"].iloc[-2])
            change_today = ((last_close - prev_close) / prev_close) * 100.0 if prev_close else 0.0

            if len(hist) >= 6:
                five_day_close = float(hist["Close"].iloc[-6])
                change_5d = ((last_close - five_day_close) / five_day_close) * 100.0 if five_day_close else 0.0
            else:
                change_5d = change_today

            # Volume ratio vs the trailing average, excluding today
            last_vol = float(hist["Volume"].iloc[-1])
            avg_vol = float(hist["Volume"].iloc[:-1].mean())
            vol_ratio = (last_vol / avg_vol) if avg_vol > 0 else 1.0

            last_dt = hist.index[-1].to_pydatetime()
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            age_days = (datetime.now(timezone.utc) - last_dt).total_seconds() / 86400.0
            stale = age_days > MARKET_MAX_STALE_DAYS
            if stale:
                print(f"[WARN] {key}: last price is {age_days:.1f} days old; "
                      f"ignoring price action for scoring.")

            result = {
                "symbol": symbol,
                "price": round(last_close, 2),
                "change_today_pct": round(change_today, 2),
                "change_5d_pct": round(change_5d, 2),
                "volume_ratio": round(vol_ratio, 2),
                "as_of": last_dt.date().isoformat(),
                "stale": stale,
                "available": True,
            }

    except Exception as e:
        print(f"[WARN] Error fetching market data for {symbol}: {e}")
        result = _empty(symbol)

    _CACHE[key] = (time.time(), result)
    return result


if __name__ == "__main__":
    for s in ["RELIANCE", "BEL", "INFY", "NOSUCHTICKER"]:
        m = check_stock_market(s)
        flag = "" if not m["stale"] else "  [STALE]"
        print(f"{s:14} ₹{m['price']:<10} today {m['change_today_pct']:+.2f}% | "
              f"5d {m['change_5d_pct']:+.2f}% | vol {m['volume_ratio']}x | "
              f"as_of {m['as_of']} | available={m['available']}{flag}")

    t0 = time.perf_counter()
    check_stock_market("RELIANCE")
    print(f"\nCached re-read took {(time.perf_counter() - t0) * 1000:.1f} ms (should be ~0).")
