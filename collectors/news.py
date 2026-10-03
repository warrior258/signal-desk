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

NEWS_QUERIES = [
    "draft notification ministry",
    "mandatory certification BIS quality control order",
    "IRDAI exposure draft OR circular",
    "ethanol blending policy tender",
    "DGFT import duty OR export curb",
    "MoRTH draft notification vehicle safety",
    "MeitY draft rules consultation",
    "TRAI consultation paper telecom",
]

def build_news_feed_url(query: str) -> str:
    encoded = quote_plus(query)
    return f"https://news.google.com/rss/search?q={encoded}&hl=en-IN&gl=IN&ceid=IN:en"

def collect() -> list[dict]:
    """
    Collects policy news from mainstream and regulatory press feeds.
    Returns list of document dicts.
    """
    collected = []
    seen_urls = set()

    for q in NEWS_QUERIES:
        url = build_news_feed_url(q)
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries:
                link = entry.get("link", "").strip()
                title = entry.get("title", "").strip()

                if not link or link in seen_urls:
                    continue
                seen_urls.add(link)

                hash_input = f"{link}:{title}".encode("utf-8")
                content_hash = hashlib.sha256(hash_input).hexdigest()
                now_iso = datetime.now(timezone.utc).isoformat()

                collected.append({
                    "source": "news",
                    "title": title,
                    "url": link,
                    "published_at": entry.get("published", now_iso),
                    "first_seen_at": now_iso,
                    "raw_text": entry.get("summary", ""),
                    "content_hash": content_hash,
                })
        except Exception as e:
            print(f"[WARN] Error fetching news feed for query '{q}': {e}")

    return collected

if __name__ == "__main__":
    print("Running News Collector...")
    items = collect()
    print(f"News Collector found {len(items)} items.")
    for it in items[:3]:
        print(f"- [{it['source']}] {it['title'][:70]}... ({it['url'][:45]}...)")
