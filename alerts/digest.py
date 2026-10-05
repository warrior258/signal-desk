import html
import sys
from collections import OrderedDict
from datetime import datetime, timezone, timedelta
from pathlib import Path

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
from analysis.scoring import PRICED_IN_PCT, level_from_score
from macro.health_report import generate_health_summary
from alerts.telegram import send_alert, split_message

ICONS = {"HIGH": "🔴", "MEDIUM": "🟠", "LOW": "⚪"}
IST = timezone(timedelta(hours=5, minutes=30))


def esc(x) -> str:
    return html.escape(str(x or ""))


def trunc(text, n: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def level_of(s: dict) -> str:
    """Single source of truth: derive level from score, never from stored text."""
    if s["direction"] not in ("positive", "negative"):
        return "LOW"
    return level_from_score(s["final_score"])


def fmt_stock(s: dict, level: str) -> str:
    dir_icon = "📈" if s["direction"] == "positive" else "📉"
    move, move_5d, vol = s.get("change_today_pct"), s.get("change_5d_pct"), s.get("volume_ratio")

    market_parts = []
    if move is not None:
        market_parts.append(f"Today {move:+.1f}%")
    if move_5d is not None:
        market_parts.append(f"5d {move_5d:+.1f}%")
    if vol is not None:
        market_parts.append(f"Vol {vol:.1f}x")

    market = f"\n   📊 {' | '.join(market_parts)}" if market_parts else ""

    sign = 1 if s["direction"] == "positive" else -1
    priced_in_warn = ""
    if move_5d is not None and (move_5d * sign >= PRICED_IN_PCT):
        priced_in_warn = f"\n   ⚠️ already {move_5d:+.1f}% in 5d (likely priced in)"

    return (
        f"{ICONS[level]} {dir_icon} <b>{esc(s['symbol'])}</b> (Score {s['final_score']} | {level}){market}{priced_in_warn}\n"
        f"   {esc(trunc(s['reason'], 160))}"
    )


def build_daily_digest(hours: int = 24) -> list[str]:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

    with db.get_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT s.symbol, s.direction, s.reason, s.final_score,
                   s.change_today_pct, s.change_5d_pct, s.volume_ratio,
                   a.stage, a.issuing_body, d.title, d.url
            FROM signals s
            JOIN analyses a ON s.analysis_id = a.id
            JOIN documents d ON a.document_id = d.id
            WHERE s.alerted_at >= ?
            ORDER BY s.final_score DESC, s.id DESC
            LIMIT 30;
        """, (cutoff,))
        rows = [dict(r) for r in cur.fetchall()]

    # Group stocks under their policy document
    groups = OrderedDict()
    for s in rows:
        groups.setdefault(s["url"], {"meta": s, "stocks": []})["stocks"].append(s)

    blocks, watch = [], []
    for g in groups.values():
        m, lines = g["meta"], []
        for s in g["stocks"]:
            lvl = level_of(s)
            if lvl == "LOW":
                watch.append(s["symbol"])
            else:
                lines.append(fmt_stock(s, lvl))
        if not lines:
            continue
        stage = esc((m["stage"] or "other").replace("_", " ").upper())
        blocks.append(
            f"📄 <b>{esc(trunc(m['title'], 90))}</b>\n"
            f"Stage: {stage} | {esc(m['issuing_body'])}\n"
            + "\n".join(lines)
            + f'\n🔗 <a href="{esc(m["url"])}">Source</a>'
        )

    today = datetime.now(IST).strftime("%d %b %Y")
    parts = [f"🌅 <b>PRE-MARKET DAILY DIGEST — {today}</b>\n\n{generate_health_summary()}"]

    if blocks:
        parts.append("📋 <b>Top Policy Signals (Last 24h)</b>")
        parts += blocks
    else:
        parts.append("📋 <i>No MEDIUM/HIGH policy signals in the last 24h.</i>")

    if watch:
        parts.append("⚪ <b>Watch only (low score / unclear direction):</b> " + ", ".join(sorted(set(watch))))

    parts.append("💡 <i>From official public sources. Open the source link and validate before trading.</i>")
    return split_message("\n\n".join(parts))


def dispatch_daily_digest() -> bool:
    ok = True
    for msg in build_daily_digest():
        ok = bool(send_alert(msg)) and ok
    return ok


if __name__ == "__main__":
    import sys as _sys
    from alerts.telegram import is_configured

    msgs = build_daily_digest()
    print("\n\n-----\n\n".join(msgs))
    print(f"\n[{len(msgs)} message(s) built]")

    if "--send" not in _sys.argv:
        print("[INFO] Preview only. Pass --send to dispatch to Telegram.")
    elif not is_configured():
        print("[INFO] Telegram credentials not set in .env; cannot send.")
    else:
        print("Digest dispatched!" if dispatch_daily_digest() else "Failed to dispatch digest.")
