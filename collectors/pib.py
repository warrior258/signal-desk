import hashlib
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus
import feedparser

root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

PIB_QUERIES = [
    "site:pib.gov.in cabinet approves",
    "site:pib.gov.in draft policy OR notification",
    "site:pib.gov.in mandatory OR standard",
    "site:pib.gov.in PLI OR subsidy OR incentive",
    "site:pib.gov.in duty OR tariff OR import export",
]

def build_pib_feed_url(query: str) -> str:
    encoded = quote_plus(query)
    return f"https://news.google.com/rss/search?q={encoded}&hl=en-IN&gl=IN&ceid=IN:en"

def collect() -> list[dict]:
    """
    Collects official PIB policy releases via indexed feeds.
    Returns list of document dicts.
    """
    collected = []
    seen_urls = set()

    for q in PIB_QUERIES:
        url = build_pib_feed_url(q)
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries:
                link = entry.get("link", "").strip()
                title = entry.get("title", "").strip()

                if not link or link in seen_urls:
                    continue
                seen_urls.add(link)

                # Compute unique hash based on URL and title
                hash_input = f"{link}:{title}".encode("utf-8")
                content_hash = hashlib.sha256(hash_input).hexdigest()
                now_iso = datetime.now(timezone.utc).isoformat()

                collected.append({
                    "source": "pib",
                    "title": title,
                    "url": link,
                    "published_at": entry.get("published", now_iso),
                    "first_seen_at": now_iso,
                    "raw_text": entry.get("summary", ""),
                    "content_hash": content_hash,
                })
        except Exception as e:
            print(f"[WARN] Error fetching PIB feed for query '{q}': {e}")

    return collected

if __name__ == "__main__":
    print("Running PIB Collector...")
    items = collect()
    print(f"PIB Collector found {len(items)} items.")
    for it in items[:3]:
        print(f"- [{it['source']}] {it['title'][:70]}... ({it['url'][:45]}...)")
