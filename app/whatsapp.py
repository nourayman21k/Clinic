import base64
import os
import requests
from dotenv import load_dotenv

from .models import Patient

load_dotenv()

OPENWA_URL = os.getenv("OPENWA_URL")
OPENWA_API_KEY = os.getenv("OPENWA_API_KEY")
OPENWA_SESSION_ID = os.getenv("OPENWA_SESSION_ID")


def _normalize_eg_phone(phone: str) -> str:
    """Mirrors the notebook's normalizer: local Egyptian format (leading 0,
    any number of pasted-on extra zeros) -> international MSISDN. Anything
    already international or unrecognized passes through unchanged."""
    digits = "".join(c for c in (phone or "") if c.isdigit())
    stripped = digits.lstrip("0")
    if stripped.startswith("20") and len(stripped) == 12:
        return stripped
    if stripped.startswith("1") and len(stripped) == 10:
        return "20" + stripped
    return digits


def _chat_id_for(patient: Patient) -> str:
    """Prefers the patient's real stored WhatsApp chat id (handles @lid
    contacts); falls back to a phone-built @c.us id otherwise. Mirrors the
    notebook's get_whatsapp_target, simplified: this module only sends to
    patients the conversational agent already knows, never resolves LIDs
    itself."""
    if patient.whatsapp_chat_id:
        return patient.whatsapp_chat_id
    return f"{_normalize_eg_phone(patient.phone)}@c.us"


def send_whatsapp_text(patient: Patient, text: str) -> bool:
    """Best-effort send for proactive backend messages (reminders, no-show
    follow-ups, re-engagement nudges) -- NOT the conversational agent, which
    lives in the notebook and replies to a patient's own message regardless
    of this flag (opt-in/out only gates UNSOLICITED outbound messages).
    Never raises: a scheduler job or a request handler must not fail just
    because one WhatsApp send didn't go through."""
    if patient.whatsapp_opt_in is False:
        print(f"WhatsApp send skipped for patient {patient.id}: opted out.")
        return False
    if not (OPENWA_URL and OPENWA_API_KEY and OPENWA_SESSION_ID):
        print("WhatsApp send skipped: OpenWA env vars not configured.")
        return False
    try:
        resp = requests.post(
            f"{OPENWA_URL}/api/sessions/{OPENWA_SESSION_ID}/messages/send-text",
            headers={"X-API-Key": OPENWA_API_KEY, "Content-Type": "application/json"},
            json={"chatId": _chat_id_for(patient), "text": text},
            timeout=15,
        )
        if resp.ok:
            return True
        print(f"WhatsApp send failed for patient {patient.id}: HTTP {resp.status_code} {resp.text[:200]}")
        return False
    except requests.RequestException as e:
        print(f"WhatsApp send failed for patient {patient.id}: {type(e).__name__}: {e}")
        return False


def send_whatsapp_document(patient: Patient, pdf_bytes: bytes, filename: str, caption: str = "") -> bool:
    """Best-effort send of a PDF (e.g. a payment receipt) as a WhatsApp document
    attachment. Same opt-in-respecting, never-raises contract as
    send_whatsapp_text -- a failed document send must never fail whatever
    request triggered it (e.g. collecting a payment)."""
    if patient.whatsapp_opt_in is False:
        print(f"WhatsApp document send skipped for patient {patient.id}: opted out.")
        return False
    if not (OPENWA_URL and OPENWA_API_KEY and OPENWA_SESSION_ID):
        print("WhatsApp document send skipped: OpenWA env vars not configured.")
        return False
    try:
        resp = requests.post(
            f"{OPENWA_URL}/api/sessions/{OPENWA_SESSION_ID}/messages/send-document",
            headers={"X-API-Key": OPENWA_API_KEY, "Content-Type": "application/json"},
            json={
                "chatId": _chat_id_for(patient),
                "base64": base64.b64encode(pdf_bytes).decode(),
                "filename": filename,
                "caption": caption,
                "mimetype": "application/pdf",
            },
            timeout=20,
        )
        if resp.ok:
            return True
        print(f"WhatsApp document send failed for patient {patient.id}: HTTP {resp.status_code} {resp.text[:200]}")
        return False
    except requests.RequestException as e:
        print(f"WhatsApp document send failed for patient {patient.id}: {type(e).__name__}: {e}")
        return False
