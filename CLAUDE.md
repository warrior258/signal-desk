# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Personal intelligence bot that ingests Indian government policy releases (PIB + policy news RSS), resolves them to listed NSE stocks via an LLM, scores the resulting trade signals, and pushes Telegram alerts plus an 8:30 AM IST pre-market digest. Pure Python, no web service, SQLite-backed.

## Commands

All commands run from the repo root. On Windows the venv interpreter is used directly rather than activating:

```powershell
.\venv\Scripts\python.exe main.py --collect-only --limit 5   # collect + filter only, no API keys needed
.\venv\Scripts\python.exe main.py --dry-run --limit 3        # full pipeline, no alerts sent
.\venv\Scripts\python.exe main.py --once --limit 5           # one live cycle, dispatches alerts
.\venv\Scripts\python.exe main.py --digest                   # build + send the daily digest now
.\venv\Scripts\python.exe main.py --test-telegram            # credential smoke test
.\venv\Scripts\python.exe main.py --schedule                 # blocking scheduler (15 min cycle + 08:30 IST digest)
```

Every entry point degrades instead of crashing when credentials are absent: without
`GEMINI_API_KEY` a cycle collects, filters and queues documents then stops before the
LLM; without Telegram credentials dispatch logs a warning and returns False.

Setup: `pip install -r requirements.txt`, then copy `.env.example` to `.env` and fill `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`, `GEMINI_API_KEY`.

### Testing

There is no test framework. Every module is runnable standalone and its `if __name__ == "__main__"` block is the de facto unit test for that module — this is how you exercise a single component:

```powershell
.\venv\Scripts\python.exe analysis\scoring.py          # scoring assertions
.\venv\Scripts\python.exe analysis\stock_matcher.py    # match/no-match cases incl. unlisted bodies
.\venv\Scripts\python.exe processing\filter.py         # keyword pre-filter cases
.\venv\Scripts\python.exe alerts\telegram.py           # message-splitting assertions
.\venv\Scripts\python.exe analysis\market_check.py     # live yfinance quotes + cache check
.\venv\Scripts\python.exe collectors\feeds.py          # shared fetch, shows how many stale items were dropped
.\venv\Scripts\python.exe macro\fetch_market.py        # macro fetch + DB write
.\venv\Scripts\python.exe alerts\digest.py             # renders the digest (preview only; --send to dispatch)
.\venv\Scripts\python.exe db.py                        # create/migrate schema
```

`scoring.py`, `stock_matcher.py`, `filter.py` and `telegram.py` assert expected results and
need no network or credentials — they are the fast feedback loop. The rest hit live feeds
or yfinance. `llm.py` needs `GEMINI_API_KEY` and exits cleanly with a message when it is absent.

When adding a module, keep this convention: a `__main__` block that prints or asserts
something meaningful, and that does not send anything outward unless explicitly asked.

## Architecture

Two pipelines converge on Telegram:

**Policy pipeline** (`main.py:run_cycle`, every 15 min) — collect → dedupe/filter → LLM → stock match → market check → score → alert:

1. **`collectors/pib_direct.py` is the primary source.** It scrapes PIB's English listing (`allRel.aspx?reg=3&lang=1`) for PRIDs, then fetches each release page (`PressReleasePage.aspx?PRID=`) and extracts the full body from `#PdfDiv` plus the "Posted On:" timestamp. This matters more than anything else in the pipeline: Google News hands over ~30 characters (a headline), while PIB serves 1,000–13,000 characters of actual policy text. It returns `None` (not `[]`) when the listing is unreachable, so callers can distinguish that from "nothing new"; `collect(is_known=db.exists)` skips stored PRIDs so release pages are never re-fetched.
2. `collectors/news.py` covers non-PIB regulatory news (IRDAI, TRAI, DGFT…) and `collectors/pib.py` survives only as a headline-only fallback when PIB itself is down. Both are query lists delegating to `collectors/feeds.py`, which fetches **Google News RSS**, applies the freshness cutoff, and sorts newest-first. Each item gets a SHA-256 `content_hash` — `"pib:{prid}"` for direct releases, `"{url}:{title}"` for feed items — which is the dedupe key and a UNIQUE column.
2. `db.exists(hash)` drops repeats; survivors are inserted via `db.save_document` immediately (even if they fail the filter) so the corpus is complete.
3. `processing/filter.py:passes_filter` is a cheap keyword gate over `config.ACTION_WORDS`, with an `IGNORE_WORDS` ceremonial-noise blocklist that is itself overridden by a few strong policy terms.
4. If nothing new passes, the cycle falls back to `db.get_unanalyzed_documents()` — a persistent backlog queue of `passed_filter=1 AND analyzed=0` rows. Only `--limit` items reach the LLM per cycle.
5. `analysis/llm.py:analyze_document` asks Gemini for a strict JSON object and returns `(parsed_dict, raw_json_str)`. It tries each model in `MODELS_TO_TRY` in order and swallows all errors until one succeeds. The prompt is the contract — the `companies[]` schema (`direction`, `relevance`, `revenue_mechanism`, `reason`) drives scoring downstream, so prompt edits and scoring edits travel together.
6. `analysis/stock_matcher.py:match` resolves a company name to an NSE symbol against `data/EQUITY_L.csv` (auto-downloaded from NSE archives on first use) using exact lookup then rapidfuzz. **Companies that don't match are silently dropped** — no signal, no row.
7. `analysis/market_check.py` pulls 1-month yfinance history for `{SYMBOL}.NS`; on any failure it returns a dict with `available: False` rather than raising, and scoring skips market adjustments for it.
8. `analysis/scoring.py:calculate_signal_score` produces the final score. Alerts fire only for `HIGH`/`MEDIUM`.

**Macro pipeline** (`macro/`, runs as part of the digest) — `fetch_market.py` pulls Brent/USDINR/VIX/Nifty from yfinance, applies per-indicator sanity checks (plausibility range, staleness warning) and a per-indicator `evaluate_status` rule, persists to `indicators`/`indicator_values`, and `health_report.py` renders the green/amber/red block that heads the digest.

**Digest** (`alerts/digest.py`) queries the last 24h of `signals` joined to `analyses` and `documents`, groups stocks under their source document, and splits output into ≤4000-char Telegram messages via `chunk()`.

### Freshness rules

Google News RSS is **relevance-ranked, not date-ranked**: an unfiltered query returns
items years old, and `when:` only improves the mix rather than guaranteeing it. So:

- The authoritative cut is the local publish-date check in `collectors/feeds.py` against `config.MAX_DOC_AGE_HOURS`. Treat `config.FEED_WINDOW` (`when:2d`) as a hint only — never rely on it alone.
- `published_at` is stored as an **ISO UTC string** derived from `published_parsed`, not the raw RSS date string, so it sorts and compares correctly. Anything writing that column must keep the format.
- Documents reach the LLM newest-first, and the backlog queue is bounded by `config.BACKLOG_MAX_AGE_HOURS` so a quiet day doesn't spend the budget on week-old items.
- `check_stock_market` returns `as_of` and `stale`; `calculate_signal_score` skips all market adjustments when `stale` is set. Don't score off a price that isn't current.

### Evidence rules

An LLM handed only a headline still answers in full, with `confidence: high` and
specific-sounding mechanisms recalled from training rather than read from the document.
That output is indistinguishable from real analysis, so the pipeline treats evidence
volume as a first-class input:

- `calculate_signal_score(..., evidence_chars=N)` subtracts `THIN_EVIDENCE_PENALTY` and **caps the level below HIGH** when `N < config.MIN_EVIDENCE_CHARS` (400). Never let a headline produce a top-conviction signal.
- `format_evidence_line()` prints the character count and source in every alert, so a reader can tell inference from reading.
- `rank_score()` in `processing/filter.py` decides LLM spend order: a keyword in the *title* is worth 3, the same word buried in a long body 1 (capped), full documents get +2. Recency is only the tie-break.
- `passes_filter` checks `IGNORE_WORDS` against the **title only**. Once bodies run to thousands of words, an incidental "cricket" would otherwise discard a real notification.

### Invariants worth preserving

- **Score is the single source of truth for level.** `scoring.level_from_score` is the only place thresholds live, and `digest.level_of` deliberately re-derives the level from the stored `final_score` rather than trusting the stored `alert_level` text. Don't introduce a second thresholding path.
- **`direction` outside `positive`/`negative` short-circuits to LOW** in both scoring and the digest — "unclear" is a watch item, never a trade signal.
- Scoring knobs (`HIGH_THRESHOLD`, `MEDIUM_THRESHOLD`, `VOLUME_SPIKE`, `PRICED_IN_PCT`, `RELEVANCE_PENALTY`, `STAGE_BONUSES`) are module constants in `analysis/scoring.py`, intended to be tuned from backtests. `CONNECTION_BONUS_ENABLED` is an intentionally dormant feature flag.
- Both `main.py` and `llm.py` accept the newer unified `companies[]` list *and* legacy `companies_positive`/`companies_negative` keys. Keep both paths when touching either.
- Timestamps are stored as UTC ISO strings; the scheduler and the digest render in IST (`Asia/Kolkata` / `timedelta(hours=5, minutes=30)`).
- Telegram messages are HTML `parse_mode`, so every interpolated value must go through `html.escape` (see `format_alert` and `digest.esc`).

### Conventions and gotchas

- **No `__init__.py` anywhere** — packages are implicit namespace packages. Every module instead prepends the repo root to `sys.path` at import time, which is what makes both `python main.py` and `python analysis/scoring.py` work. Copy that header into new modules.
- Every module also carries a Windows `sys.stdout.reconfigure(encoding="utf-8")` guard, because the output is heavily emoji-laden and would crash on cp1252 consoles.
- Macro thresholds live in `MACRO_CONFIG` in `macro/fetch_market.py`, not in `config.py`.
- Schema changes live in `db.py:init_db`, which is idempotent: `CREATE TABLE IF NOT EXISTS` plus a loop of `ALTER TABLE ... ADD COLUMN` wrapped in `try/except sqlite3.OperationalError` for migrations. Follow that pattern instead of writing migration scripts.
- `data/bot.db` is gitignored; `data/EQUITY_L.csv` is committed.
- Collectors and market fetchers never raise — they log `[WARN]` and return partial/empty results so one dead feed can't kill a cycle. Preserve that.
