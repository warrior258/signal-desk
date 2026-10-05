# Policy Signal Bot & Economy Health Monitor

A lightweight personal intelligence bot tracking Indian government policy activity (PIB releases, Ministry notifications, regulatory drafts) and macro economic pressures (Food, Fuel, Fertiliser, Finance), resolving commercial opportunities on listed NSE stocks, and delivering an **8:30 AM Pre-Market Daily Digest** and high-conviction alerts via Telegram.

---

## Architecture Overview

```
┌────────────────────────────────────────────────────────┐
│                   MAIN RUNNER / SCHEDULER              │
└────────────────────────────────────────────────────────┘
          │                                  │
          ▼                                  ▼
┌──────────────────┐               ┌────────────────────┐
│ POLICY PIPELINE  │               │ MACRO MONITOR      │
│ (Every 15 min)   │               │ (Daily & Weekly)   │
└──────────────────┘               └────────────────────┘
          │                                  │
          ▼                                  ▼
┌──────────────────┐               ┌────────────────────┐
│ PIB & News RSS   │               │ Brent, USD/INR,    │
│ Dedupe + Filter  │               │ India VIX, Nifty   │
└──────────────────┘               └────────────────────┘
          │                                  │
          ▼                                  ▼
┌──────────────────┐               ┌────────────────────┐
│ Gemini AI +      │               │ Status:            │
│ Stock Matcher    │               │ 🟢 Green           │
│ Market Check     │               │ 🟠 Amber           │
│ Final Scoring    │               │ 🔴 Red             │
└──────────────────┘               └────────────────────┘
          │                                  │
          └────────────────┬─────────────────┘
                           ▼
          ┌──────────────────────────────────┐
          │     TELEGRAM DISPATCH            │
          │  - 8:30 AM Daily Digest          │
          │  - High/Medium Real-time Alerts  │
          └──────────────────────────────────┘
```

---

## Quick Start Guide

### 1. Activate Environment
In PowerShell:
```powershell
.\venv\Scripts\Activate.ps1
```

### 2. Verify Credentials
Check `.env`:
```ini
TELEGRAM_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
GEMINI_API_KEY=your_gemini_api_key
```

Test Telegram connectivity:
```powershell
.\venv\Scripts\python.exe main.py --test-telegram
```

### 3. Run a Dry Run
Fetches feeds, checks deduplication, pre-filters keywords, runs Gemini LLM extraction, matches NSE stocks, and performs market checks without sending alerts:
```powershell
.\venv\Scripts\python.exe main.py --dry-run --limit 3
```

No API keys yet? `--collect-only` exercises collection, freshness filtering and
deduplication with no LLM calls and no alerts:
```powershell
.\venv\Scripts\python.exe main.py --collect-only --limit 5
```

### 4. Send the 8:30 AM Pre-Market Daily Digest
Generates the Economy Health snapshot + top policy signals from the last 24h and sends it to Telegram:
```powershell
.\venv\Scripts\python.exe main.py --digest
```

### 5. Run One Live Cycle
Scans for new policy items and sends instant alerts for high/medium conviction signals:
```powershell
.\venv\Scripts\python.exe main.py --once --limit 5
```

### 6. Start the 24/7 Scheduler
Runs the policy ingestion cycle every 15 minutes, and triggers the Daily Digest every morning at 08:30 AM IST:
```powershell
.\venv\Scripts\python.exe main.py --schedule
```

---

## Project Structure

```
policy-bot/
├── .env                       # Credentials (Tokens, Keys)
├── config.py                  # Paths, thresholds & action keywords
├── db.py                      # SQLite database manager (documents, analyses, signals)
├── main.py                    # Master CLI orchestrator & APScheduler
├── requirements.txt           # Project dependencies
├── alerts/
│   ├── telegram.py            # Telegram alert dispatcher
│   └── digest.py              # 8:30 AM consolidated daily digest
├── collectors/
│   ├── pib_direct.py          # PRIMARY: scrapes full PIB release text
│   ├── feeds.py               # Shared RSS fetch + freshness cutoff + dedupe
│   ├── pib.py                 # Headline-only fallback if PIB is unreachable
│   └── news.py                # Non-PIB regulatory news (IRDAI, TRAI, DGFT…)
├── processing/
│   └── filter.py              # Fast keyword action pre-filter
├── analysis/
│   ├── llm.py                 # Gemini structured JSON policy extraction
│   ├── stock_matcher.py       # NSE company fuzzy matcher (EQUITY_L.csv)
│   ├── market_check.py        # Live NSE price, 5d momentum & volume ratio
│   └── scoring.py             # Composite signal scoring (HIGH/MEDIUM/LOW)
├── macro/
│   ├── fetch_market.py        # Brent, USD/INR, VIX, Nifty tracker
│   └── health_report.py       # Economy status generator
└── data/
    ├── bot.db                 # SQLite database
    └── EQUITY_L.csv           # NSE official stock symbols
```

---

## Evidence Quality

Google News RSS returns a headline and a markup snippet — around 30 characters of
actual content. Gemini answers anyway, with `confidence: high` and specific-sounding
mechanisms drawn from training data rather than from the document. The result reads
exactly like real analysis.

So the bot reads PIB directly (`collectors/pib_direct.py`), pulling 1,000–13,000
characters of real release text, and treats evidence volume as a scoring input:

| Guard | Effect |
|---|---|
| `MIN_EVIDENCE_CHARS` (400) | Below it, −3.0 to the score and the level is capped below HIGH |
| Evidence line in every alert | States the character count and source, so inference is visible |
| `rank_score()` | LLM budget goes to the most policy-dense documents, not just the newest |
| Contradiction check | Falling ≥5% against the call on flat volume costs −2.0 |

---

## Data Freshness

Google News RSS ranks results by **relevance, not date** — an unfiltered policy
query returns articles from years ago, and the `when:` operator still leaks older
items through. Acting on those is the difference between a tradeable signal and
yesterday's news, so freshness is enforced in three places:

| Control | Where | Default |
|---|---|---|
| `when:` hint sent to Google News | `config.FEED_WINDOW` | `when:2d` |
| Hard cutoff on each item's publish date | `config.MAX_DOC_AGE_HOURS` | 36 h |
| Age limit on the unanalyzed backlog queue | `config.BACKLOG_MAX_AGE_HOURS` | 48 h |

Collected items are sorted newest-first, so `--limit` always spends the LLM budget
on the freshest policy documents. Market quotes carry an `as_of` date and a
`stale` flag; stale prices are ignored by scoring rather than silently treated as
today's move.

---

## Database Tables (`data/bot.db`)

* `documents`: Raw ingested articles with `first_seen_at`, source, URL, and SHA-256 hash.
* `analyses`: Structured LLM insights (stage, impact score, sectors, positive/negative companies).
* `signals`: Matched NSE symbols, directions, prices at signal, volume ratios, and final scores.
* `indicators` & `indicator_values`: Macro time-series data with green/amber/red status.
