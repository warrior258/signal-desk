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

    # 3. Market checks
    m = market_data or {}
    if m.get("available"):
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

    return round(score, 1), level_from_score(score)


if __name__ == "__main__":
    assert calculate_signal_score(8, "announcement", "positive", "primary", {})[1] == "MEDIUM"
    assert calculate_signal_score(8, "announcement", "unclear")[1] == "LOW"
    assert calculate_signal_score(8, "announcement", "positive", "secondary", {})[0] == 6.0
    m = {"available": True, "volume_ratio": 2.5, "change_today_pct": 1.0, "change_5d_pct": 2.1}
    print("Scoring tests passed successfully!")
    print("Draft primary:", calculate_signal_score(8, "draft", "positive", "primary", m))