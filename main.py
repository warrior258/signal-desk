import argparse
import sys
import time
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
from collectors import pib, news
from processing.filter import passes_filter
from analysis.llm import analyze_document
from analysis.stock_matcher import match
from analysis.market_check import check_stock_market
from analysis.scoring import calculate_signal_score, level_from_score
from alerts.telegram import send_alert
from alerts.digest import dispatch_daily_digest

import html

def format_alert(doc: dict, analysis: dict, scored_stocks: list[dict], overall_level: str, max_score: float) -> str:
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

        move = m.get("change_today_pct", 0.0)
        move_5d = m.get("change_5d_pct", 0.0)
        vol = m.get("volume_ratio", 1.0)
        price = m.get("price", 0.0)

        sign = 1 if s["direction"] == "positive" else -1
        priced_in_note = ""
        if move_5d is not None and (move_5d * sign >= 8.0):
            priced_in_note = f"\n    ⚠️ <i>already {move_5d:+.1f}% in 5d (likely priced in)</i>"

        stock_line = (
            f"  {dir_icon} <b>{sym}</b> ({dir_name}{rel_tag}) — Score: <b>{s['score']}</b>\n"
            f"    <i>{reason}</i>\n"
            f"    📊 Price: ₹{price} | Today: {move:+.1f}% | 5d: {move_5d:+.1f}% | Vol: {vol:.1f}x{priced_in_note}"
        )
        stock_blocks.append(stock_line)

    stocks_text = "\n\n".join(stock_blocks) if stock_blocks else "  ℹ️ Sector-wide impact, no specific company listed."

    pos_sec = html.escape(", ".join(analysis.get("sectors_positive", [])) or "None")
    neg_sec = html.escape(", ".join(analysis.get("sectors_negative", [])) or "None")

    alert_text = (
        f"<b>{icon} | Final Score: {max_score}</b>\n"
        f"🏛 <b>Stage:</b> {stage} | <b>Body:</b> {body}\n\n"
        f"📝 <b>Summary:</b> {summary}\n\n"
        f"<b>Affected Companies & Market Check:</b>\n{stocks_text}\n\n"
        f"🌐 <b>Positive Sectors:</b> {pos_sec}\n"
        f"🔻 <b>Negative Sectors:</b> {neg_sec}\n"
        f"⏱ <b>Horizon:</b> {horizon}\n"
        f"🔗 <a href='{url}'>Source Link</a>"
    )
    return alert_text

def run_cycle(dry_run: bool = False, max_items_to_analyze: int = 5):
    """
    Executes one ingestion, analysis, market check, and scoring cycle.
    """
    db.init_db()
    print("=" * 60)
    print(f"Starting policy pipeline cycle (Dry-Run: {dry_run})...")
    print("=" * 60)

    # 1. Collect
    print("[1/4] Collecting from PIB & News RSS...")
    raw_docs = pib.collect() + news.collect()
    print(f"Total raw items fetched: {len(raw_docs)}")

    # 2. Filter & Deduplicate
    new_docs = []
    passed_docs = []
    for doc in raw_docs:
        h = doc["content_hash"]
        if db.exists(h):
            continue
        doc_id = db.save_document(doc)
        doc["id"] = doc_id
        new_docs.append(doc)

        if passes_filter(doc["title"], doc.get("raw_text", "")):
            db.mark_document_filtered(doc_id, True)
            passed_docs.append(doc)

    print(f"[2/4] New documents: {len(new_docs)} | Passed keyword action filter in this batch: {len(passed_docs)}")

    items_to_process = passed_docs
    if not items_to_process:
        pending = db.get_unanalyzed_documents(limit=max_items_to_analyze)
        if pending:
            print(f"[INFO] Processing {len(pending)} pending unanalyzed documents from database queue.")
            items_to_process = pending

    if not items_to_process:
        print("[INFO] No new or pending policy documents to analyze.")
        return

    # 3. Analyze with LLM
    print(f"[3/4] Analyzing top {min(len(items_to_process), max_items_to_analyze)} high-relevance items with Gemini...")
    analyzed_count = 0
    signals_found = 0

    for doc in items_to_process[:max_items_to_analyze]:
        doc_id = doc["id"]
        title = doc["title"]
        text = doc.get("raw_text", "")

        print(f"\nAnalyzing: {title[:75]}...")
        analysis, raw_json = analyze_document(title, text)
        db.mark_document_analyzed(doc_id)

        if not analysis:
            continue

        analyzed_count += 1
        analysis_id = db.save_analysis(doc_id, analysis, raw_json)
        base_score = float(analysis.get("impact_score", 1))
        stage = analysis.get("document_stage", "other")

        # 4. Resolve Stocks & Run Market Check + Scoring
        scored_stocks = []
        max_score = base_score
        overall_level = "LOW"

        # Handle unified companies list or backward-compatible keys
        companies_to_process = []
        if "companies" in analysis and isinstance(analysis["companies"], list):
            companies_to_process = analysis["companies"]
        else:
            for d in ["positive", "negative"]:
                for c in analysis.get(f"companies_{d}", []):
                    c["direction"] = d
                    companies_to_process.append(c)

        for c in companies_to_process:
            c_name = c.get("name", "")
            reason = c.get("reason", "")
            direction = (c.get("direction") or "positive").lower().strip()
            relevance = (c.get("relevance") or "primary").lower().strip()
            mechanism = c.get("revenue_mechanism", "").strip()

            if mechanism and mechanism.lower() not in reason.lower():
                full_reason = f"{reason} (Mechanism: {mechanism})"
            else:
                full_reason = reason

            sym = match(c_name)
            if not sym:
                continue

            # Query real-time market data
            market = check_stock_market(sym)

            # Compute final composite score
            final_score, level = calculate_signal_score(
                base_impact_score=base_score,
                stage=stage,
                direction=direction,
                relevance=relevance,
                market_data=market
            )

            # Track highest conviction
            if level == "HIGH" or (level == "MEDIUM" and overall_level != "HIGH"):
                overall_level = level
            if final_score > max_score and direction in ("positive", "negative"):
                max_score = final_score

            scored_stocks.append({
                "symbol": sym,
                "name": c_name,
                "direction": direction,
                "relevance": relevance,
                "reason": full_reason,
                "market": market,
                "score": final_score,
                "level": level
            })

            # Persist to database
            db.save_signal(
                analysis_id=analysis_id,
                symbol=sym,
                direction=direction,
                reason=full_reason,
                price=market.get("price", 0.0),
                change_today=market.get("change_today_pct", 0.0),
                volume_ratio=market.get("volume_ratio", 1.0),
                connected=0,
                score=final_score,
                alert_level=level,
                change_5d=market.get("change_5d_pct", 0.0),
                relevance=relevance
            )

        if scored_stocks:
            signals_found += len(scored_stocks)
            print(f"  👉 Matched & Scored Stocks: {[(s['symbol'], s['score'], s['level'], s['direction'], s['relevance']) for s in scored_stocks]}")

        # 5. Dispatch Alert
        if not dry_run and overall_level in ("HIGH", "MEDIUM"):
            alert_msg = format_alert(doc, analysis, scored_stocks, overall_level, max_score)
            send_alert(alert_msg)
            print(f"  🔔 Telegram alert dispatched (Level: {overall_level}, Score: {max_score}).")
        elif dry_run:
            print(f"  [Dry-Run] Max Score: {max_score} | Level: {overall_level} | Matched: {len(scored_stocks)}")

    print("\n" + "=" * 60)
    print(f"Cycle completed. Analyzed: {analyzed_count} | Signals Matched: {signals_found}")
    print("=" * 60)

def start_scheduler(interval_minutes: int = 15):
    """Starts the continuous background scheduler."""
    scheduler = BlockingScheduler(timezone="Asia/Kolkata")

    # Cycle job every N minutes
    scheduler.add_job(
        run_cycle,
        "interval",
        minutes=interval_minutes,
        kwargs={"dry_run": False, "max_items_to_analyze": 5},
        id="policy_cycle_job"
    )

    # 8:30 AM IST Daily Digest job
    scheduler.add_job(
        dispatch_daily_digest,
        "cron",
        hour=8,
        minute=30,
        id="daily_digest_job"
    )

    print("=" * 60)
    print("🚀 Policy Signal Bot Scheduler Started!")
    print(f"- Policy collector cycle: Every {interval_minutes} minutes")
    print("- Pre-Market Daily Digest: Every day at 08:30 AM IST")
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
    parser.add_argument("--limit", type=int, default=5, help="Maximum number of passed items to analyze in this run")
    parser.add_argument("--test-telegram", action="store_true", help="Send a test message to Telegram")
    parser.add_argument("--digest", action="store_true", help="Generate and send the 8:30 AM Daily Digest right now")
    parser.add_argument("--schedule", action="store_true", help="Run continuously with APScheduler")

    args = parser.parse_args()

    if args.test_telegram:
        send_alert("<b>✅ Policy Signal Bot</b>: Connection verified.")
        print("Telegram test sent.")
        return

    if args.digest:
        print("Dispatching Daily Digest to Telegram...")
        ok = dispatch_daily_digest()
        print("Digest sent successfully!" if ok else "Failed to send digest.")
        return

    if args.schedule:
        start_scheduler()
        return

    if args.once:
        run_cycle(dry_run=False, max_items_to_analyze=args.limit)
    else:
        run_cycle(dry_run=True, max_items_to_analyze=args.limit)

if __name__ == "__main__":
    main()
