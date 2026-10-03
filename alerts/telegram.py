import requests
import sys
from pathlib import Path

# Ensure root directory is in sys.path
root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

# Support utf-8 print on Windows console
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID

def send_alert(text: str, parse_mode: str = "HTML") -> bool:
    """
    Sends an alert message to the configured Telegram chat.
    Returns True if sent successfully, False otherwise.
    """
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("[WARN] Telegram credentials not configured in .env (TELEGRAM_TOKEN, TELEGRAM_CHAT_ID).")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }

    response = None
    try:
        response = requests.post(url, json=payload, timeout=15)
        response.raise_for_status()
        return True
    except requests.exceptions.RequestException as e:
        print(f"[ERROR] Failed to send Telegram alert: {e}")
        if response is not None and hasattr(response, "text"):
            print(f"[ERROR] Telegram API Response: {response.text}")
        return False

if __name__ == "__main__":
    print("Testing Telegram alert dispatch...")
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("[INFO] Please edit your .env file and set TELEGRAM_TOKEN and TELEGRAM_CHAT_ID first.")
    else:
        test_msg = (
            "🚀 <b>Policy Signal Bot — Connected!</b>\n\n"
            "This is a test notification confirming Phase 1 Telegram integration is working."
        )
        ok = send_alert(test_msg)
        if ok:
            print("✅ Telegram test message sent successfully! Check your Telegram app.")
        else:
            print("❌ Failed to send Telegram test message. Check your token and chat ID in .env.")
