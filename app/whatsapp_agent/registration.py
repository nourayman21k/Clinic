import requests

from .config import (
    BACKEND_PUBLIC_URL,
    OPENWA_API_KEY,
    OPENWA_SESSION_ID,
    OPENWA_URL,
    WHATSAPP_WEBHOOK_SECRET,
)

_WEBHOOK_URL = f"{BACKEND_PUBLIC_URL}/api/whatsapp/webhook"


def register_openwa_webhook() -> None:
    """Idempotent create-or-update: points OpenWA at our webhook endpoint on
    every backend startup, so there's no manual one-time setup step and the
    registration self-heals if the backend's URL ever changes. Never raises --
    a registration hiccup must not prevent the backend itself from starting."""
    if not (OPENWA_URL and OPENWA_API_KEY and OPENWA_SESSION_ID):
        print("[whatsapp_agent] Skipping webhook registration: OpenWA env vars not configured.")
        return
    if not WHATSAPP_WEBHOOK_SECRET:
        print("[whatsapp_agent] Skipping webhook registration: WHATSAPP_WEBHOOK_SECRET not set.")
        return

    base = f"{OPENWA_URL}/api/sessions/{OPENWA_SESSION_ID}/webhooks"
    headers = {"X-API-Key": OPENWA_API_KEY, "Content-Type": "application/json"}
    body = {
        "url": _WEBHOOK_URL,
        "events": ["message.received"],
        "secret": WHATSAPP_WEBHOOK_SECRET,
        "retryCount": 3,
    }

    try:
        resp = requests.get(base, headers=headers, timeout=10)
        resp.raise_for_status()
        existing = next((w for w in resp.json() if w.get("url") == _WEBHOOK_URL), None)

        if existing:
            requests.put(f"{base}/{existing['id']}", headers=headers, json=body, timeout=10).raise_for_status()
            print(f"[whatsapp_agent] Webhook registration updated ({_WEBHOOK_URL}).")
        else:
            requests.post(base, headers=headers, json=body, timeout=10).raise_for_status()
            print(f"[whatsapp_agent] Webhook registered ({_WEBHOOK_URL}).")
    except requests.RequestException as e:
        print(f"[whatsapp_agent] Webhook registration failed: {type(e).__name__}: {e}")
