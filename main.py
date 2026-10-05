import argparse
import html
import sys
from pathlib import Path

from apscheduler.schedulers.blocking import BlockingScheduler

# Ensure root directory is in sys.path
root_dir = Path(__file__).resolve().parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

import db
from collectors import pib, pib_direct, news
from processing.filter import passes_filter, rank_score
from analysis import llm
from analysis.stock_matcher import match
from analysis.market_check import check_stock_market
from analysis.scoring import PRICED_IN_PCT, calculate_signal_score
from config import MIN_EVIDENCE_CHARS
from alerts.telegram import send_alert, is_configured as telegram_configured
from alerts.digest import dispatch_daily_digest


def format_market_line(m: dict) -> str:
    """One-line market snapshot, honest about missing or stale data."""
    if not m.get("available"):
        return "    📊 <i>market data unavailable</i>"

    line = (f"    📊 Price: ₹{m.get('price', 0.0)} | "
            f"Today: {m.get('change_today_pct', 0.0):+.1f}% | "
            f"5d: {m.get('change_5d_pct', 0.0):+.1f}% | "
            f"Vol: {m.get('volume_ratio', 1.0):.1f}x")
    if m.get("stale"):
        line += f"\n    ⏳ <i>prices as of {m.get('as_of')} — not current</i>"
    return line


def format_evidence_line(doc: dict) -> str:
    """
    States how much source text backed the analysis. A reader must be able to
    tell a conclusion drawn from a full release apart from one inferred from a
    headline, because the two read identically once the model has written them.
    """
    chars = len(doc.get("raw_text", "") or "")
    source = doc.get("source", "unknown")
    if chars < MIN_EVIDENCE_CHARS:
        return (f"📑 <b>Evidence:</b> ⚠️ headline only ({chars} chars) — "
                f"reasoning is inferred, not read from the document")
    return f"📑 <b>Evidence:</b> full text ({chars:,} chars from {html.escape(source)})"


def format_alert(doc: dict, analysis: dict, scored_stocks: list[dict],
                 overall_level: str, max_score: float) -> str:
    """Formats an enriched, actionable HTML alert for Telegram with HTML escaping."""
    stage = html.escape((analysis.get("document_stage") or "Notification").upper().replace("_", " "))
    body = html.escape(analysis.get("issuing_body") or "Government of India")
    summary = html.escape(analysis.get("summary", ""))
    url = html.escape(doc.get("url", ""))
    horizon = html.escape(analysis.get("time_horizon", "weeks"))

    icon = "🔴 HIGH" if overall_level == "HIGH" else "🟠 MEDIUM" if overall_level == "MEDIUM" else "⚪ LOW"

    stock_blocks = []
    for s in scored_stocks:
        dir_icon = "📈" if s["direction"] == "positive" else "📉" if s["direction"] == "negative" else "⚪"
        m = s["market"]
        sym = html.escape(s["symbol"])
        dir_name = html.escape(s["direction"].capitalize())
        rel_tag = f" [{s.get('relevance', 'primary').capitalize()}]" if s.get("relevance") != "primary" else ""
        reason = html.escape(s["reason"])

        move_5d = m.get("change_5d_pct")
        sign = 1 if s["direction"] == "positive" else -1
        priced_in_note = ""
        if m.get("available") and not m.get("stale") and move_5d is not None \
                and move_5d * sign >= PRICED_IN_PCT:
            priced_in_note = f"\n    ⚠️ <i>already {move_5d:+.1f}% in 5d (likely priced in)</i>"

        stock_blocks.append(
            f"  {dir_icon} <b>{sym}</b> ({dir_name}{rel_tag}) — Score: <b>{s['score']}</b>\n"
            f"    <i>{reason}</i>\n"
            f"{format_market_line(m)}{priced_in_note}"
        )

    stocks_text = "\n\n".join(stock_blocks) if stock_blocks else "  ℹ️ Sector-wide impact, no specific company listed."

    pos_sec = html.escape(", ".join(analysis.get("sectors_positive", [])) or "None")
    neg_sec = html.escape(", ".join(analysis.get("sectors_negative", [])) or "None")

    return (
        f"<b>{icon} | Final Score: {max_score}</b>\n"
        f"🏛 <b>Stage:</b> {stage} | <b>Body:</b> {body}\n\n"
        f"📝 <b>Summary:</b> {summary}\n\n"
        f"<b>Affected Companies &amp; Market Check:</b>\n{stocks_text}\n\n"
        f"🌐 <b>Positive Sectors:</b> {pos_sec}\n"
        f"🔻 <b>Negative Sectors:</b> {neg_sec}\n"
        f"⏱ <b>Horizon:</b> {horizon}\n"
        f"{format_evidence_line(doc)}\n"
        f"🔗 <a href='{url}'>Source Link</a>"
    )


def collect_and_store() -> tuple[list[dict], int]:
    """
    Collects from every source, stores new documents, and returns
    (documents that passed the keyword filter, count of new documents),
    newest first.
    """
    # PIB directly: full release text, which is what the LLM actually needs.
    # Only if the site is unreachable do we fall back to the headline-only
    # Google News mirror of the same source.
    pib_docs = pib_direct.collect(is_known=db.exists)
    if pib_docs is None:
        print("[INFO] PIB unreachable; falling back to Google News (headline-only).")
        pib_docs = pib.collect()

    raw_docs = pib_docs + news.collect()
    # Collectors each sort internally; re-sort so the merged list is newest-first
    # and the LLM budget is always spent on the freshest policy items.
    raw_docs.sort(key=lambda d: d.get("published_at", ""), reverse=True)
    print(f"Total fresh items fetched: {len(raw_docs)}")

    new_docs, passed_docs = [], []
    for doc in raw_docs:
        if db.exists(doc["content_hash"]):
            continue
        doc["id"] = db.save_document(doc)
        if doc["id"] < 0:
            # Could not store or resolve the row; skip rather than write
            # analyses and signals pointing at a non-existent document.
            continue
        new_docs.append(doc)

        if passes_filter(doc["title"], doc.get("raw_text", "")):
            db.mark_document_filtered(doc["id"], True)
            passed_docs.append(doc)

    # Highest policy relevance first, recency as the tie-break. Spending the
    # LLM budget strictly newest-first means analysing photo captions while a
    # tariff notification from two hours earlier waits.
    passed_docs.sort(
        key=lambda d: (rank_score(d["title"], d.get("raw_text", "")), d.get("published_at", "")),
        reverse=True,
    )
    return passed_docs, len(new_docs)


def analyze_and_score(doc: dict) -> tuple[dict | None, list[dict], str, float]:
    """
    Runs LLM extraction, stock matching, market checks and scoring for one document.
    Returns (analysis, scored_stocks, overall_level, max_score).
    """
    analysis, raw_json = llm.analyze_document(doc["title"], doc.get("raw_text", ""))
    if not analysis:
        # Leave the document unanalyzed so a transient API failure (503, rate
        # limit) is retried next cycle instead of being silently dropped.
        # The backlog's age bound stops this retrying forever.
        return None, [], "LOW", 0.0

    db.mark_document_analyzed(doc["id"])

    analysis_id = db.save_analysis(doc["id"], analysis, raw_json)
    base_score = float(analysis.get("impact_score", 1))
    stage = analysis.get("document_stage", "other")
    # How much source text the model actually saw. Drives the evidence penalty.
    evidence_chars = len(doc.get("raw_text", "") or "")

    # Accept the unified companies list or the older per-direction keys
    companies = []
    if isinstance(analysis.get("companies"), list):
        companies = analysis["companies"]
    else:
        for d in ("positive", "negative"):
            for c in analysis.get(f"companies_{d}", []):
                c["direction"] = d
                companies.append(c)

    scored_stocks = []
    overall_level = "LOW"

    for c in companies:
        direction = (c.get("direction") or "positive").lower().strip()
        relevance = (c.get("relevance") or "primary").lower().strip()
        reason = c.get("reason", "")
        mechanism = (c.get("revenue_mechanism") or "").strip()
        if mechanism and mechanism.lower() not in reason.lower():
            reason = f"{reason} (Mechanism: {mechanism})"

        sym = match(c.get("name", ""))
        if not sym:
            continue

        market = check_stock_market(sym)
        final_score, level = calculate_signal_score(
            base_impact_score=base_score,
            stage=stage,
            direction=direction,
            relevance=relevance,
            market_data=market,
            evidence_chars=evidence_chars,
        )

        if level == "HIGH" or (level == "MEDIUM" and overall_level != "HIGH"):
            overall_level = level

        scored_stocks.append({
            "symbol": sym, "name": c.get("name", ""), "direction": direction,
            "relevance": relevance, "reason": reason, "market": market,
            "score": final_score, "level": level,
        })

        db.save_signal(
            analysis_id=analysis_id, symbol=sym, direction=direction, reason=reason,
            price=market.get("price", 0.0), change_today=market.get("change_today_pct", 0.0),
            volume_ratio=market.get("volume_ratio", 1.0), connected=0,
            score=final_score, alert_level=level,
            change_5d=market.get("change_5d_pct", 0.0), relevance=relevance,
        )

    # The headline score must come from real signals, not the raw impact score
    max_score = max((s["score"] for s in scored_stocks), default=base_score)
    return analysis, scored_stocks, overall_level, max_score


def run_cycle(dry_run: bool = False, max_items_to_analyze: int = 5, collect_only: bool = False):
    """Executes one ingestion, analysis, market check, and scoring cycle."""
    db.init_db()
    print("=" * 60)
    print(f"Starting policy pipeline cycle (Dry-Run: {dry_run})...")
    print("=" * 60)

    print("[1/4] Collecting from PIB & News RSS...")
    passed_docs, new_count = collect_and_store()
    print(f"[2/4] New documents: {new_count} | Passed keyword action filter: {len(passed_docs)}")

    items = passed_docs
    if not items:
        # Pull a wider pool than we can analyse, then rank it, so the queue is
        # worked in order of policy relevance rather than arrival order.
        pool = db.get_unanalyzed_documents(limit=max_items_to_analyze * 4)
        pool.sort(
            key=lambda d: (rank_score(d["title"], d.get("raw_text", "")), d.get("published_at", "")),
            reverse=True,
        )
        items = pool
        if items:
            print(f"[INFO] Processing {min(len(items), max_items_to_analyze)} "
                  f"of {len(items)} queued documents, highest policy relevance first.")

    if not items:
        print("[INFO] No new or pending policy documents to analyze.")
        return

    items = items[:max_items_to_analyze]

    if collect_only:
        print(f"\n[collect-only] {len(items)} item(s) would be analyzed:")
        for d in items:
            print(f"  - {str(d.get('published_at'))[:16]} | {d['title'][:70]}")
        return

    if not llm.is_available():
        print("\n[INFO] GEMINI_API_KEY is not set, so analysis is skipped.")
        print(f"[INFO] {len(items)} item(s) are queued and will be analyzed once a key is configured:")
        for d in items:
            print(f"  - {str(d.get('published_at'))[:16]} | {d['title'][:70]}")
        return

    print(f"[3/4] Analyzing top {len(items)} items with Gemini...")
    analyzed_count = 0
    signals_found = 0

    for doc in items:
        print(f"\nAnalyzing: {doc['title'][:75]}...")
        analysis, scored_stocks, overall_level, max_score = analyze_and_score(doc)
        if not analysis:
            continue
        analyzed_count += 1

        if scored_stocks:
            signals_found += len(scored_stocks)
            summary = [(s["symbol"], s["score"], s["level"], s["direction"], s["relevance"])
                       for s in scored_stocks]
            print(f"  👉 Matched & Scored: {summary}")

        if dry_run:
            print(f"  [Dry-Run] Max Score: {max_score} | Level: {overall_level} | Matched: {len(scored_stocks)}")
        elif overall_level in ("HIGH", "MEDIUM"):
            send_alert(format_alert(doc, analysis, scored_stocks, overall_level, max_score))
            print(f"  🔔 Telegram alert dispatched (Level: {overall_level}, Score: {max_score}).")

    print("\n" + "=" * 60)
    print(f"[4/4] Cycle completed. Analyzed: {analyzed_count} | Signals Matched: {signals_found}")
    print("=" * 60)


def start_scheduler(interval_minutes: int = 15):
    """Starts the continuous background scheduler."""
    scheduler = BlockingScheduler(timezone="Asia/Kolkata")

    # coalesce + max_instances keep a slow cycle from stacking up behind itself
    scheduler.add_job(
        run_cycle, "interval", minutes=interval_minutes,
        kwargs={"dry_run": False, "max_items_to_analyze": 5},
        id="policy_cycle_job", coalesce=True, max_instances=1,
        misfire_grace_time=300,
    )

    # The digest is worth sending a little late if the cycle job overran
    scheduler.add_job(
        dispatch_daily_digest, "cron", hour=8, minute=30,
        id="daily_digest_job", coalesce=True, misfire_grace_time=1800,
    )

    print("=" * 60)
    print("🚀 Policy Signal Bot Scheduler Started!")
    print(f"- Policy collector cycle: Every {interval_minutes} minutes")
    print("- Pre-Market Daily Digest: Every day at 08:30 AM IST")
    if not telegram_configured():
        print("- ⚠️  Telegram is not configured; alerts will not be delivered.")
    if not llm.is_available():
        print("- ⚠️  GEMINI_API_KEY is not set; documents will be collected but not analyzed.")
    print("Press Ctrl+C to stop.")
    print("=" * 60)

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        print("\nScheduler stopped.")


def main():
    parser = argparse.ArgumentParser(description="Policy Signal Bot Pipeline")
    parser.add_argument("--dry-run", action="store_true", help="Process items without sending Telegram alerts")
    parser.add_argument("--once", action="store_true", help="Run a single live cycle and dispatch alerts")
    parser.add_argument("--collect-only", action="store_true",
                        help="Collect and filter only; no LLM calls or alerts (works without API keys)")
    parser.add_argument("--limit", type=int, default=5, help="Maximum number of passed items to analyze in this run")
    parser.add_argument("--test-telegram", action="store_true", help="Send a test message to Telegram")
    parser.add_argument("--digest", action="store_true", help="Generate and send the 8:30 AM Daily Digest right now")
    parser.add_argument("--schedule", action="store_true", help="Run continuously with APScheduler")
    args = parser.parse_args()

    if args.test_telegram:
        if send_alert("<b>✅ Policy Signal Bot</b>: Connection verified."):
            print("Telegram test sent.")
        else:
            print("Telegram test failed. Check TELEGRAM_TOKEN and TELEGRAM_CHAT_ID in .env.")
        return

    if args.digest:
        print("Dispatching Daily Digest to Telegram...")
        print("Digest sent successfully!" if dispatch_daily_digest() else "Failed to send digest.")
        return

    if args.schedule:
        start_scheduler()
        return

    run_cycle(
        dry_run=not args.once,
        max_items_to_analyze=args.limit,
        collect_only=args.collect_only,
    )


if __name__ == "__main__":
    main()
