import sys
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

def check_stock_market(symbol: str) -> dict:
    """
    Fetches latest price, short-term momentum, and volume ratio for an NSE stock.
    Appends .NS for Indian equity on Yahoo Finance.
    """
    ticker_symbol = f"{symbol.strip().upper()}.NS"
    try:
        t = yf.Ticker(ticker_symbol)
        hist = t.history(period="1mo")
        if hist.empty or len(hist) < 2:
            return {
                "symbol": symbol,
                "price": 0.0,
                "change_today_pct": 0.0,
                "change_5d_pct": 0.0,
                "volume_ratio": 1.0,
                "available": False
            }

        last_close = float(hist["Close"].iloc[-1])
        prev_close = float(hist["Close"].iloc[-2])
        change_today = ((last_close - prev_close) / prev_close) * 100.0

        if len(hist) >= 6:
            five_day_close = float(hist["Close"].iloc[-6])
            change_5d = ((last_close - five_day_close) / five_day_close) * 100.0
        else:
            change_5d = change_today

        # Volume ratio vs 20-day average
        last_vol = float(hist["Volume"].iloc[-1])
        avg_vol = float(hist["Volume"].iloc[:-1].mean()) if len(hist) > 1 else last_vol
        vol_ratio = (last_vol / avg_vol) if avg_vol > 0 else 1.0

        return {
            "symbol": symbol,
            "price": round(last_close, 2),
            "change_today_pct": round(change_today, 2),
            "change_5d_pct": round(change_5d, 2),
            "volume_ratio": round(vol_ratio, 2),
            "available": True
        }

    except Exception as e:
        print(f"[WARN] Error fetching market data for {symbol}: {e}")
        return {
            "symbol": symbol,
            "price": 0.0,
            "change_today_pct": 0.0,
            "change_5d_pct": 0.0,
            "volume_ratio": 1.0,
            "available": False
        }

if __name__ == "__main__":
    for s in ["RELIANCE", "BEL", "TMCV", "INFY"]:
        m = check_stock_market(s)
        print(f"{s}: Price ₹{m['price']} | Today: {m['change_today_pct']}% | 5d: {m['change_5d_pct']}% | Vol: {m['volume_ratio']}x")
