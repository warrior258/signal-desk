import sys
from pathlib import Path

root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from collectors.feeds import collect_feeds

PIB_QUERIES = [
    "site:pib.gov.in cabinet approves",
    "site:pib.gov.in draft policy OR notification",
    "site:pib.gov.in mandatory OR standard",
    "site:pib.gov.in PLI OR subsidy OR incentive",
    "site:pib.gov.in duty OR tariff OR import export",
]


def collect() -> list[dict]:
    """Collects recent official PIB policy releases. Newest first."""
    return collect_feeds(PIB_QUERIES, "pib")


if __name__ == "__main__":
    print("Running PIB Collector...")
    items = collect()
    print(f"PIB Collector found {len(items)} fresh items.")
    for it in items[:3]:
        print(f"- {it['published_at'][:16]} | {it['title'][:65]}")
