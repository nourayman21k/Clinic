import asyncio
import hashlib
import hmac
import json

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request

from . import db
from .config import MY_OWN_NUMBER, WHATSAPP_WEBHOOK_SECRET
from .dispatcher import process_message

router = APIRouter(prefix="/api/whatsapp", tags=["whatsapp-agent"])


def _verify_signature(raw_body: bytes, signature_header: str | None) -> bool:
    if not WHATSAPP_WEBHOOK_SECRET:
        return False
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(WHATSAPP_WEBHOOK_SECRET.encode(), raw_body, hashlib.sha256).hexdigest()
    got = signature_header.removeprefix("sha256=")
    return hmac.compare_digest(expected, got)


def _claim_message(idempotency_key: str) -> bool:
    """Atomically marks this delivery as claimed. Returns True if this is the
    first time we've seen it (caller should process it), False if it's a
    duplicate delivery (OpenWA retry or replay) that must be a no-op.
    This -- not OpenWA's advisory idempotency key -- is the actual dedup,
    since OpenWA does not guarantee at-most-once delivery."""
    db.ensure_conn()
    cur = db.conn.cursor()
    cur.execute(
        "INSERT INTO processed_whatsapp_messages (idempotency_key) VALUES (%s) ON CONFLICT DO NOTHING",
        (idempotency_key,),
    )
    return cur.rowcount == 1


def _is_relevant(data: dict) -> bool:
    chat_id = data.get("chatId") or data.get("from") or ""
    return (
        not data.get("fromMe", False)
        and not data.get("isGroup", False)  # explicit field, belt-and-suspenders with the @g.us check below
        and data.get("type") in ("text", "voice")
        and "@g.us" not in chat_id
        and "@newsletter" not in chat_id
        and "@broadcast" not in chat_id
        and MY_OWN_NUMBER not in chat_id
    )


def _to_handler_message(data: dict) -> dict:
    """Adapts a webhook IncomingMessage payload to the flat dict shape
    handler.get_message_text / handle_incoming_openwa_message expect."""
    return {
        "id": data.get("id"),
        "from": data.get("chatId") or data.get("from"),
        "type": data.get("type"),
        "body": data.get("body"),
        "fromMe": data.get("fromMe", False),
        "media": data.get("media"),
    }


@router.post("/webhook")
async def receive_openwa_webhook(request: Request, background_tasks: BackgroundTasks,
                                  x_openwa_signature: str | None = Header(default=None)):
    raw_body = await request.body()
    if not _verify_signature(raw_body, x_openwa_signature):
        raise HTTPException(status_code=401, detail="Invalid or missing webhook signature")

    payload = json.loads(raw_body)
    idempotency_key = payload.get("idempotencyKey")
    if not idempotency_key:
        raise HTTPException(status_code=400, detail="Missing idempotencyKey")

    # _claim_message does blocking psycopg2 I/O -- run it off the event loop so one
    # request's DB round-trip never stalls every other concurrent request's ACK
    # (this is what a k6 load test at real concurrency actually caught: p95 ACK
    # latency ballooned to ~8.7s at 25 concurrent VUs before this fix).
    claimed = await asyncio.to_thread(_claim_message, idempotency_key)
    if not claimed:
        return {"status": "duplicate, ignored"}

    if payload.get("event") != "message.received":
        return {"status": "ignored (not a message event)"}

    data = payload.get("data") or {}
    if not _is_relevant(data):
        return {"status": "ignored (not a relevant incoming message)"}

    background_tasks.add_task(process_message, _to_handler_message(data))
    return {"status": "accepted"}
