import sys
from pathlib import Path

root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from config import MIN_EVIDENCE_CHARS

STAGE_BONUSES = {
    "draft": 2.0, "consultation": 2.0, "bill_introduced": 2.0,
    "final_notification": 1.0, "bill_passed": 1.0,
    "announcement": 0.0, "other": 0.0,
}

# Tunable constants: change these from backtest data, not gut feeling
HIGH_THRESHOLD = 12.0
MEDIUM_THRESHOLD = 8.0
VOLUME_SPIKE = 2.0
PRICED_IN_PCT = 8.0
CONTRADICTION_PCT = 5.0        # move against the call that counts as disagreement
THIN_EVIDENCE_PENALTY = 3.0    # applied when only a headline was available
CONNECTION_BONUS_ENABLED = False  # turn on only after connected signals are validated

RELEVANCE_PENALTY = {
    "primary": 0.0,
    "secondary": 2.0,
    "indirect": 4.0,
}


def level_from_score(score: float) -> str:
    """Single source of truth for alert levels."""
    if score >= HIGH_THRESHOLD:
        return "HIGH"
    if score >= MEDIUM_THRESHOLD:
        return "MEDIUM"
    return "LOW"


def calculate_signal_score(
    base_impact_score: float,
    stage: str,
    direction: str,
    relevance: str = "primary",
    market_data: dict | None = None,
    connected: bool = False,
    connection_confidence: str = "low",
    evidence_chars: int | None = None,
) -> tuple[float, str]:
    score = float(base_impact_score or 0)

    # Unclear direction = watch only, never a trade signal
    direction = (direction or "").lower().strip()
    if direction not in ("positive", "negative"):
        return round(score, 1), "LOW"

    # Relevance penalty: non-primary beneficiaries receive a discount
    rel_key = (relevance or "primary").lower().strip()
    score -= RELEVANCE_PENALTY.get(rel_key, 0.0)

    # 1. Stage bonus (earlier stage = more edge)
    score += STAGE_BONUSES.get((stage or "other").lower().strip(), 0.0)

    # 2. Connection bonus (off until validated)
    if CONNECTION_BONUS_ENABLED and connected:
        score += 3.0 if connection_confidence == "high" else 1.0

    # 3. Market checks (skipped on stale data: a price from last week says
    #    nothing about how the market is reacting to today's policy)
    m = market_data or {}
    if m.get("available") and not m.get("stale"):
        sign = 1 if direction == "positive" else -1
        vol = m.get("volume_ratio") or 1.0
        change_today = m.get("change_today_pct") or 0.0
        change_5d = m.get("change_5d_pct") or 0.0

        # Volume spike counts only if today's move agrees with the signal
        if vol >= VOLUME_SPIKE and change_today * sign > 0:
            score += 1.0

        # Priced in: already moved 8%+ in the expected direction
        if change_5d * sign >= PRICED_IN_PCT:
            score -= 3.0

        # Market disagrees: moving against the call with no volume behind it.
        # Catches the case where a "catalyst" is actually old news.
        if change_5d * sign <= -CONTRADICTION_PCT and vol < VOLUME_SPIKE:
            score -= 2.0

    # 4. Evidence check. A score derived from a headline alone is a guess
    #    dressed up as analysis, so penalise it and never let it reach HIGH.
    thin_evidence = evidence_chars is not None and evidence_chars < MIN_EVIDENCE_CHARS
    if thin_evidence:
        score -= THIN_EVIDENCE_PENALTY

    level = level_from_score(score)
    if thin_evidence and level == "HIGH":
        level = "MEDIUM"

    return round(score, 1), level


if __name__ == "__main__":
    fresh = {"available": True, "stale": False,
             "volume_ratio": 2.5, "change_today_pct": 1.0, "change_5d_pct": 2.1}
    stale = {**fresh, "stale": True}
    priced_in = {**fresh, "change_5d_pct": 12.0}

    # base scoring
    assert calculate_signal_score(8, "announcement", "positive", "primary", {})[1] == "MEDIUM"
    assert calculate_signal_score(8, "announcement", "unclear")[1] == "LOW"
    assert calculate_signal_score(8, "announcement", "positive", "secondary", {})[0] == 6.0
    assert calculate_signal_score(8, "announcement", "positive", "indirect", {})[0] == 4.0

    # stage bonus: an early-stage draft beats a plain announcement
    assert calculate_signal_score(8, "draft", "positive", "primary", {})[0] == 10.0

    # confirming volume spike adds, stale market data must change nothing
    assert calculate_signal_score(8, "draft", "positive", "primary", fresh)[0] == 11.0
    assert calculate_signal_score(8, "draft", "positive", "primary", stale)[0] == 10.0

    # already-moved names are discounted
    assert calculate_signal_score(8, "draft", "positive", "primary", priced_in)[0] == 8.0

    # market moving against the call on flat volume is a discount, not a non-event
    against = {"available": True, "stale": False, "volume_ratio": 1.0,
               "change_today_pct": -1.2, "change_5d_pct": -7.7}
    assert calculate_signal_score(8, "draft", "positive", "primary", against)[0] == 8.0
    # ...unless real volume is behind the move
    assert calculate_signal_score(8, "draft", "positive", "primary",
                                  {**against, "volume_ratio": 2.5})[0] == 10.0

    # headline-only evidence is penalised and capped below HIGH
    assert calculate_signal_score(8, "draft", "positive", "primary", {}, evidence_chars=28)[0] == 7.0
    assert calculate_signal_score(14, "draft", "positive", "primary", {}, evidence_chars=28)[1] == "MEDIUM"
    assert calculate_signal_score(14, "draft", "positive", "primary", {}, evidence_chars=2600)[1] == "HIGH"

    print("All scoring tests passed.")
    print("Draft primary (fresh market):", calculate_signal_score(8, "draft", "positive", "primary", fresh))