import sys
import time
from pathlib import Path

import requests

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

# Telegram rejects messages over 4096 characters; stay under it.
TG_LIMIT = 4000


def is_configured() -> bool:
    """True if Telegram credentials are present."""
    return bool(TELEGRAM_TOKEN and TELEGRAM_CHAT_ID)


def split_message(text: str, limit: int = TG_LIMIT) -> list[str]:
    """
    Splits text into Telegram-sized messages, preferring blank-line boundaries
    and falling back to a hard cut for any single oversized block.
    """
    messages, current = [], ""
    for block in text.split("\n\n"):
        while len(block) > limit:
            messages.append(block[:limit])
            block = block[limit:]
        if current and len(current) + len(block) + 2 > limit:
            messages.append(current)
            current = block
        else:
            current = f"{current}\n\n{block}" if current else block
    if current:
        messages.append(current)
    return messages


def _post(text: str, parse_mode: str) -> bool:
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }

    for attempt in (1, 2):
        response = None
        try:
            response = requests.post(url, json=payload, timeout=15)
            # Honour rate limiting once before giving up
            if response.status_code == 429 and attempt == 1:
                retry_after = response.json().get("parameters", {}).get("retry_after", 2)
                print(f"[WARN] Telegram rate limited; retrying in {retry_after}s.")
                time.sleep(min(float(retry_after), 30.0))
                continue
            response.raise_for_status()
            return True
        except requests.exceptions.RequestException as e:
            if attempt == 2:
                print(f"[ERROR] Failed to send Telegram alert: {e}")
                if response is not None:
                    print(f"[ERROR] Telegram API Response: {response.text}")
                return False
            time.sleep(1)
    return False


def send_alert(text: str, parse_mode: str = "HTML") -> bool:
    """
    Sends an alert to the configured Telegram chat, splitting oversized
    messages. Returns True only if every part was delivered.
    """
    if not is_configured():
        print("[WARN] Telegram credentials not configured in .env (TELEGRAM_TOKEN, TELEGRAM_CHAT_ID).")
        return False

    return all(_post(part, parse_mode) for part in split_message(text))


if __name__ == "__main__":
    # Splitting is testable without credentials
    long_text = "\n\n".join(f"block {i} " + "x" * 300 for i in range(40))
    parts = split_message(long_text)
    assert all(len(p) <= TG_LIMIT for p in parts), "split produced an oversized part"
    assert split_message("short") == ["short"]
    assert len(split_message("y" * 9000)) == 3
    print(f"Split tests passed ({len(parts)} parts from {len(long_text)} chars).")

    if not is_configured():
        print("[INFO] Set TELEGRAM_TOKEN and TELEGRAM_CHAT_ID in .env to send a live test.")
    else:
        ok = send_alert("🚀 <b>Policy Signal Bot — Connected!</b>\n\nTelegram integration is working.")
        print("✅ Telegram test message sent!" if ok else "❌ Failed to send Telegram test message.")
