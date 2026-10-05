import html
import sys
from pathlib import Path

root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from macro.fetch_market import fetch_macro_indicators


def generate_health_summary(indicators: list[dict] = None) -> str:
    """
    Builds a structured HTML text block of economy health indicators.
    Shows 'STRESS ALERT' only if at least one indicator is red; otherwise 'Economy health'.
    """
    if indicators is None:
        indicators = fetch_macro_indicators()

    if not indicators:
        return "<i>Macro data temporarily unavailable.</i>"

    lines = []
    has_red = False

    for ind in indicators:
        st = ind["status"]
        icon = "🔴" if st == "red" else "🟠" if st == "amber" else "🟢"
        if st == "red":
            has_red = True

        name = html.escape(str(ind["name"]))
        area = html.escape(str(ind["area"]))
        val = html.escape(str(ind["value"]))
        chg = ind["change_30d_pct"]

        # For Nifty the status is driven by drawdown, so show that number
        detail = f"{chg:+.1f}% 30d"
        if ind.get("key") == "nifty":
            detail = f"{ind.get('drawdown_30d_pct', 0.0):+.1f}% from 30d high"

        stale_flag = " ⏳<i>stale</i>" if ind.get("stale") else ""
        lines.append(f"{icon} <b>{area}</b>: {name} = <code>{val}</code> ({detail}){stale_flag}")

    summary_header = "🚨 <b>MACRO STRESS ALERT</b>" if has_red else "🩺 <b>Economy health</b>"
    return f"{summary_header}\n" + "\n".join(lines)


if __name__ == "__main__":
    report = generate_health_summary()
    print(report)
