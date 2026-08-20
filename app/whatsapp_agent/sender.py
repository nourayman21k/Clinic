import re

import requests

from . import db
from .config import OPENWA_API_KEY, OPENWA_SESSION_ID, OPENWA_URL


def normalize_eg_phone(phone: str) -> str:
    """Normalize a patient-typed phone number to the international MSISDN format
    WhatsApp JIDs require (e.g. "01156646810" / "001156646810" -> "201156646810").
    Handles any number of pasted-on leading zeros. Anything that still doesn't
    look like an Egyptian mobile is returned as-is (better to fail loudly on a
    real send than guess wrong)."""
    digits = re.sub(r"\D", "", phone or "")
    stripped = digits.lstrip("0")
    if stripped.startswith("20") and len(stripped) == 12:
        return stripped
    if stripped.startswith("1") and len(stripped) == 10:
        return "20" + stripped
    return digits


_lid_resolution_cache: dict[str, str] = {}  # chat_id -> resolved jid, or chat_id itself if unresolvable


def resolve_lid_to_real_jid(chat_id: str) -> str:
    """If chat_id is an @lid (privacy-protected contact), resolve it to the real
    phone number via OpenWA's dedicated phone-resolution endpoint. Falls back to
    the original chat_id if resolution isn't available or fails.

    Uses GET /contacts/:contactId/phone (not the generic /contacts/:contactId,
    whose `number` field is the LID's own digits for an @lid contact, not the
    real MSISDN).

    This endpoint is best-effort -- OpenWA itself can return phone=null when the
    engine has never mapped this @lid to a real number; that's a real dead end
    for this lookup path. graph.handle_whatsapp_message_v2's deterministic
    self-heal is what actually recovers a usable target in that case, by linking
    whatsapp_chat_id to the patient_id the conversation itself established.

    Result is cached per chat_id for the life of the process -- an unresolvable
    @lid doesn't change moment to moment, so there's no point re-querying it on
    every message."""
    if not chat_id.endswith("@lid"):
        return chat_id
    if chat_id in _lid_resolution_cache:
        return _lid_resolution_cache[chat_id]
    resolved = chat_id  # default: unresolvable, unless proven otherwise below
    try:
        url = f"{OPENWA_URL}/api/sessions/{OPENWA_SESSION_ID}/contacts/{chat_id}/phone"
        headers = {"X-API-Key": OPENWA_API_KEY}
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.ok:
            phone = resp.json().get("phone")
            # Some OpenWA versions/contacts echo the LID's OWN digits back as
            # "phone" -- a real resolution must be a DIFFERENT number than the LID itself.
            if phone and phone not in chat_id:
                resolved = f"{phone}@c.us"
                print(f"   ℹ️ Resolved {chat_id} -> {resolved}")
    except Exception as e:
        print(f"   ⚠️ Could not resolve @lid contact {chat_id}: {e}")
    if resolved == chat_id:
        print(f"   ℹ️ {chat_id} has no known phone mapping yet — will keep using the raw chat id "
              f"(won't re-check again this session).")
    _lid_resolution_cache[chat_id] = resolved
    return resolved


def _log_failed_whatsapp_send(chat_id: str, text: str):
    """Persist failed sends so a silent-failure never just vanishes into the
    print log (failed_whatsapp_sends already exists in the live DB)."""
    cur = db.conn.cursor()
    cur.execute(
        "INSERT INTO failed_whatsapp_sends (chat_id, text) VALUES (%s, %s)",
        (chat_id, text),
    )


_working_target_cache: dict[str, str] = {}  # chat_id -> the target that last delivered successfully


def send_whatsapp_text_to_chat(chat_id: str, text: str) -> bool:
    """Candidate-based sender: try targets in order until one delivers.
      1. whatever target last worked for this chat (cached)
      2. the resolved real JID (only if genuinely different from the LID)
      3. the raw chat_id exactly as WhatsApp gave it
      4. if the chat_id is {digits}@c.us, also try {digits}@lid -- recovers the
         case where a fake @c.us was built from LID digits by a phone fallback.
    First success is cached so later sends to this chat go straight to what works."""
    candidates = []
    cached = _working_target_cache.get(chat_id)
    if cached:
        candidates.append(cached)
    resolved = resolve_lid_to_real_jid(chat_id)
    if resolved != chat_id:
        candidates.append(resolved)
    candidates.append(chat_id)
    if chat_id.endswith("@c.us"):
        candidates.append(chat_id.split("@")[0] + "@lid")
    # dedupe, keep order
    seen = set()
    candidates = [c for c in candidates if not (c in seen or seen.add(c))]

    url = f"{OPENWA_URL}/api/sessions/{OPENWA_SESSION_ID}/messages/send-text"
    headers = {"X-API-Key": OPENWA_API_KEY, "Content-Type": "application/json"}

    for target in candidates:
        try:
            resp = requests.post(url, headers=headers, json={"chatId": target, "text": text}, timeout=15)
        except requests.RequestException as e:
            print(f"   ❌ Send to {target} failed (network): {e}")
            continue
        if resp.ok:
            _working_target_cache[chat_id] = target
            if target != candidates[0]:
                print(f"   ℹ️ Delivered via fallback target {target}")
            return True
        print(f"   ❌ Send to {target} failed ({resp.status_code})")

    _log_failed_whatsapp_send(chat_id, text)
    return False


def get_whatsapp_target(patient_id: int, fallback_phone: str = None) -> str | None:
    """The ONE place that decides where to message a patient -- prefers their
    real stored whatsapp_chat_id (handles @lid), falls back to {phone}@c.us
    otherwise. All tools (confirmation / cancel / reschedule) route through this."""
    cur = db.conn.cursor()
    cur.execute("SELECT whatsapp_chat_id, phone FROM patients WHERE id = %s", (patient_id,))
    row = cur.fetchone()
    if not row:
        return f"{normalize_eg_phone(fallback_phone)}@c.us" if fallback_phone else None
    chat_id, phone = row
    return chat_id if chat_id else f"{normalize_eg_phone(phone)}@c.us"
