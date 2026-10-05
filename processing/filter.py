import re
import sys
from pathlib import Path

root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from config import ACTION_WORDS

# Routine administrative keywords to drop automatically
IGNORE_WORDS = [
    "condolences", "pays homage", "tribute", "swearing-in",
    "congratulates", "sports", "cricket", "medal", "tournament",
    "farewell", "greetings on", "wishes on", "mann ki baat",
]

# Policy terms strong enough to keep an item even if it looks ceremonial
STRONG_WORDS = [
    "quality control order", "qco", "mandatory certification",
    "tariff", "anti-dumping",
]


def _compile(words: list[str]) -> re.Pattern:
    """Word-boundary matcher: 'ban' must not match 'banking', 'bill' not 'billion'."""
    return re.compile(r"\b(?:" + "|".join(re.escape(w) for w in words) + r")\b", re.IGNORECASE)


_ACTION_RE = _compile(ACTION_WORDS)
_IGNORE_RE = _compile(IGNORE_WORDS)
_STRONG_RE = _compile(STRONG_WORDS)


def matched_action(title: str, text: str) -> str | None:
    """Returns the action keyword that let this document through, or None."""
    # Ceremonial items are identifiable from the title. Checking the body too
    # would throw away a real policy release that merely mentions a tournament
    # in passing -- a live risk now that full PIB text runs to thousands of words.
    if _IGNORE_RE.search(title) and not _STRONG_RE.search(title):
        return None

    m = _ACTION_RE.search(f"{title} {text}")
    return m.group(0).lower() if m else None


def passes_filter(title: str, text: str) -> bool:
    """True if the document looks like a policy action worth analysing."""
    return matched_action(title, text) is not None


def rank_score(title: str, text: str) -> int:
    """
    Cheap priority score for deciding which documents are worth LLM budget.

    Recency alone is a poor queue order: the newest release is as likely to be a
    photo caption as a tariff notification. A keyword in the *title* says the
    document is about a policy action; the same word buried in a long body may
    just be passing context, so it counts for much less.
    """
    title_hits = {m.group(0).lower() for m in _ACTION_RE.finditer(title)}
    body_hits = {m.group(0).lower() for m in _ACTION_RE.finditer(text)}
    strong_hits = {m.group(0).lower() for m in _STRONG_RE.finditer(f"{title} {text}")}

    score = 3 * len(title_hits) + min(len(body_hits - title_hits), 4) + 2 * len(strong_hits)
    if len(text) >= 400:          # a real document rather than a headline
        score += 2
    return score


if __name__ == "__main__":
    test_samples = [
        ("Ministry of Commerce issues mandatory certification Quality Control Order on copper products", "Details inside...", True),
        ("PM pays homage to martyrs on anniversary", "Solemn ceremony held...", False),
        ("MeitY releases draft guidelines for public consultation on electronic parts", "Stakeholders can comment...", True),
        # Substring false positives that the old filter let through:
        ("Banking stocks rally as lenders report strong quarter", "", False),
        ("Company posts billion dollar revenue", "", False),
        ("The process was necessary for urban growth", "", False),
    ]
    failures = 0
    for title, text, expected in test_samples:
        got = passes_filter(title, text)
        ok = "ok " if got == expected else "FAIL"
        if got != expected:
            failures += 1
        print(f"[{ok}] passes={got!s:5} kw={matched_action(title, text)!s:12} | {title[:52]}")
    print("All filter tests passed." if not failures else f"{failures} filter test(s) failed.")
