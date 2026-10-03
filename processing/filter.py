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

def passes_filter(title: str, text: str) -> bool:
    """
    Returns True if the document title or text contains policy action keywords
    and does not match exclusion criteria.
    """
    blob = f"{title} {text}".lower()

    # Drop obvious non-policy administrative / ceremonial press releases
    if any(ignore_term in blob for ignore_term in IGNORE_WORDS):
        # Only drop if no strong policy word is explicitly present
        if not any(strong in blob for strong in ["quality control order", "qco", "mandatory certification", "tariff", "anti-dumping"]):
            return False

    return any(word in blob for word in ACTION_WORDS)

if __name__ == "__main__":
    test_samples = [
        ("Ministry of Commerce issues mandatory certification Quality Control Order on copper products", "Details inside..."),
        ("PM pays homage to martyrs on anniversary", "Solemn ceremony held..."),
        ("MeitY releases draft guidelines for public consultation on electronic parts", "Stakeholders can comment..."),
    ]
    for title, text in test_samples:
        print(f"Title: {title[:50]}... -> Passes: {passes_filter(title, text)}")
