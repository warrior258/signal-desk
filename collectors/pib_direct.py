"""Direct PIB scraper — the primary source of real policy text.

Google News RSS only ever hands over a headline plus a markup snippet, which
left the LLM guessing at a document it had never seen. PIB serves the full
release as plain server-rendered HTML, so this collector reads the actual text:

    listing  https://pib.gov.in/allRel.aspx?reg=3&lang=1     (English, newest first)
    release  https://pib.gov.in/PressReleasePage.aspx?PRID=  (full body + posted time)

Release pages are fetched one at a time with a short delay, and only for PRIDs
that are not already stored, so a steady-state cycle makes very few requests.
"""
import hashlib
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
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

from config import MAX_DOC_AGE_HOURS, PIB_FETCH_DELAY_SECONDS, PIB_MAX_RELEASES

LISTING_URL = "https://pib.gov.in/allRel.aspx?reg=3&lang=1"
RELEASE_URL = "https://pib.gov.in/PressReleasePage.aspx?PRID={prid}"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
IST = timezone(timedelta(hours=5, minutes=30))

# "Posted On: 05 OCT 2026 3:14PM by PIB Delhi"
_POSTED_RE = re.compile(
    r"Posted On:\s*(\d{1,2}\s+[A-Za-z]{3}\s+\d{4}\s+\d{1,2}:\d{2}\s*[AP]M)", re.IGNORECASE
)


def _get(url: str, timeout: int = 25) -> str | None:
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        r.raise_for_status()
        return r.text
    except requests.exceptions.RequestException as e:
        print(f"[WARN] PIB request failed ({url[:60]}): {e}")
        return None


def list_releases() -> list[tuple[str, str]]:
    """Returns (prid, title) for each release on the English listing page."""
    page = _get(LISTING_URL)
    if not page:
        return []

    soup = BeautifulSoup(page, "html.parser")
    seen, out = set(), []
    for a in soup.find_all("a", href=True):
        m = re.search(r"PRID=(\d+)", a["href"])
        if not m:
            continue
        prid = m.group(1)
        title = a.get_text(" ", strip=True)
        if prid in seen or not title:
            continue
        seen.add(prid)
        out.append((prid, title))
    return out


def parse_posted_at(text: str) -> datetime | None:
    """Extracts the release's posted time (IST on the page) as aware UTC."""
    m = _POSTED_RE.search(text)
    if not m:
        return None
    stamp = re.sub(r"\s+", " ", m.group(1)).strip()
    try:
        dt = datetime.strptime(stamp.upper(), "%d %b %Y %I:%M%p")
    except ValueError:
        return None
    return dt.replace(tzinfo=IST).astimezone(timezone.utc)


def fetch_release(prid: str) -> tuple[str, datetime | None]:
    """Returns (body_text, posted_at_utc) for one release."""
    page = _get(RELEASE_URL.format(prid=prid))
    if not page:
        return "", None

    soup = BeautifulSoup(page, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer"]):
        tag.decompose()

    node = soup.select_one("#PdfDiv") or soup.select_one(
        ".innner-page-main-about-us-content-right-part"
    )
    if node is None:
        return "", None

    text = re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip()
    posted_at = parse_posted_at(text)

    # Drop the "Posted On: ... by PIB Delhi" furniture from the analysed text
    text = _POSTED_RE.sub(" ", text)
    text = re.sub(r"\bby PIB [A-Za-z ]+\b", " ", text)
    return re.sub(r"\s+", " ", text).strip(), posted_at


def collect(max_age_hours: int = MAX_DOC_AGE_HOURS,
            max_releases: int = PIB_MAX_RELEASES,
            is_known=None) -> list[dict] | None:
    """
    Collects recent PIB releases with their full text, newest first.

    `is_known(content_hash)` lets the caller skip releases already stored so
    their pages are never re-fetched.

    Returns None if the listing itself could not be read, so the caller can tell
    "PIB is unreachable" apart from "nothing new since last cycle".
    """
    releases = list_releases()
    if not releases:
        print("[WARN] PIB listing could not be read.")
        return None

    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    collected = []
    skipped_known = stale = failed = 0

    for prid, title in releases[:max_releases]:
        url = RELEASE_URL.format(prid=prid)
        content_hash = hashlib.sha256(f"pib:{prid}".encode("utf-8")).hexdigest()

        if is_known and is_known(content_hash):
            skipped_known += 1
            continue

        body, posted_at = fetch_release(prid)
        time.sleep(PIB_FETCH_DELAY_SECONDS)

        if not body:
            failed += 1
            continue
        if posted_at is None:
            posted_at = datetime.now(timezone.utc)
        elif posted_at < cutoff:
            stale += 1
            continue

        collected.append({
            "source": "pib_direct",
            "title": title,
            "url": url,
            "published_at": posted_at.isoformat(),
            "first_seen_at": datetime.now(timezone.utc).isoformat(),
            "raw_text": body,
            "content_hash": content_hash,
        })

    print(f"[INFO] pib_direct: {len(collected)} new releases with full text "
          f"(skipped {skipped_known} known, {stale} stale, {failed} unreadable).")
    collected.sort(key=lambda d: d["published_at"], reverse=True)
    return collected


if __name__ == "__main__":
    releases = list_releases()
    print(f"Listing returned {len(releases)} releases.")
    if not releases:
        raise SystemExit("Could not read the PIB listing.")

    prid, title = releases[0]
    body, posted = fetch_release(prid)
    print(f"\nPRID {prid}: {title[:70]}")
    print(f"Posted at (UTC): {posted}")
    print(f"Body length: {len(body)} chars")
    print(f"Body preview: {body[:300]}")

    docs = collect(max_releases=3)
    print(f"\ncollect() returned {len(docs)} documents.")
    for d in docs:
        print(f"  - {d['published_at'][:16]} | {len(d['raw_text']):>6} chars | {d['title'][:55]}")
