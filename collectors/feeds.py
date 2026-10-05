"""Shared Google News RSS fetching for all collectors.

Google News ranks search results by relevance, not by date, so a plain query
returns a mix that can include articles several years old. Two defences:
  1. a "when:" hint in the query, which improves the mix but is not reliable,
  2. a hard local cutoff on each entry's published date, which is.
"""
import hashlib
import html as html_mod
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote_plus

import feedparser
from bs4 import BeautifulSoup

root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from config import FEED_WINDOW, MAX_DOC_AGE_HOURS

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"


def build_feed_url(query: str, window: str = FEED_WINDOW) -> str:
    """Builds a Google News RSS URL for an India-localised query."""
    q = f"{query} {window}".strip() if window else query
    return f"{GOOGLE_NEWS_RSS}?q={quote_plus(q)}&hl=en-IN&gl=IN&ceid=IN:en"


def published_utc(entry) -> datetime | None:
    """Entry publish time as an aware UTC datetime, or None if the feed omits it."""
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    try:
        return datetime(*parsed[:6], tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def clean_text(raw: str) -> str:
    """RSS summaries are HTML snippets; reduce them to plain readable text."""
    if not raw:
        return ""
    text = BeautifulSoup(html_mod.unescape(raw), "html.parser").get_text(" ")
    return re.sub(r"\s+", " ", text).strip()


def collect_feeds(queries: list[str], source: str,
                  max_age_hours: int = MAX_DOC_AGE_HOURS,
                  window: str = FEED_WINDOW) -> list[dict]:
    """
    Fetches every query, drops stale and duplicate entries, and returns
    document dicts sorted newest first.
    """
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=max_age_hours)

    collected = []
    seen_urls = set()
    stale = 0
    undated = 0

    for q in queries:
        try:
            feed = feedparser.parse(build_feed_url(q, window))
        except Exception as e:
            print(f"[WARN] Error fetching {source} feed for query '{q}': {e}")
            continue

        for entry in feed.entries:
            link = (entry.get("link") or "").strip()
            title = (entry.get("title") or "").strip()
            if not link or not title or link in seen_urls:
                continue
            seen_urls.add(link)

            published = published_utc(entry)
            if published is None:
                # No date to judge by: keep it, dedupe will stop repeats.
                undated += 1
                published = now
            elif published < cutoff:
                stale += 1
                continue

            now_iso = now.isoformat()
            collected.append({
                "source": source,
                "title": title,
                "url": link,
                # ISO UTC so the value sorts and compares correctly everywhere.
                "published_at": published.isoformat(),
                "first_seen_at": now_iso,
                "raw_text": clean_text(entry.get("summary", "")),
                "content_hash": hashlib.sha256(f"{link}:{title}".encode("utf-8")).hexdigest(),
            })

    if stale or undated:
        print(f"[INFO] {source}: dropped {stale} items older than {max_age_hours}h"
              f"{f', kept {undated} undated' if undated else ''}.")

    collected.sort(key=lambda d: d["published_at"], reverse=True)
    return collected


if __name__ == "__main__":
    items = collect_feeds(["site:pib.gov.in cabinet approves"], "test")
    print(f"Fetched {len(items)} fresh items (cutoff {MAX_DOC_AGE_HOURS}h).")
    for it in items[:5]:
        print(f"- {it['published_at'][:16]} | {it['title'][:60]}")
