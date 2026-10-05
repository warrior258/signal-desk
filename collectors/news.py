import sys
from pathlib import Path

root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from collectors.feeds import collect_feeds

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


def collect() -> list[dict]:
    """Collects recent policy news from mainstream and regulatory press. Newest first."""
    return collect_feeds(NEWS_QUERIES, "news")


if __name__ == "__main__":
    print("Running News Collector...")
    items = collect()
    print(f"News Collector found {len(items)} fresh items.")
    for it in items[:3]:
        print(f"- {it['published_at'][:16]} | {it['title'][:65]}")
