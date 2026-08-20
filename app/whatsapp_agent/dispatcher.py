import asyncio

from . import db, sender
from .config import WHATSAPP_AGENT_CONCURRENCY
from .handler import handle_incoming_openwa_message

_chat_locks: dict[str, asyncio.Lock] = {}
_semaphore = asyncio.Semaphore(WHATSAPP_AGENT_CONCURRENCY)


def _process_sync(msg: dict) -> None:
    """Runs on a worker thread (via asyncio.to_thread) since the whole pipeline
    below it -- psycopg2, the LLM clients, requests -- is blocking sync code."""
    db.ensure_conn()  # the pooler/NAT can drop an idle connection between patients
    preview_in = (msg.get("body") or "")[:80] if msg.get("type") == "text" else "[voice note]"
    print(f"📩 New {msg['type']} message from {msg['from']}: {preview_in}")
    try:
        reply = handle_incoming_openwa_message(msg)
        preview = (reply[:120] + "…") if reply and len(reply) > 120 else reply
        print(f"✅ [{msg['type']}] from {msg['from']}: replied -> {preview}")
    except Exception as e:
        # No retry: a failed delivery must never be retried by re-running the
        # business logic (that's exactly the double-reply bug the notebook's
        # FIX 22 already fixed once) -- log once, apologize once, move on.
        print(f"❌ Error processing {msg['from']}: {type(e).__name__}: {e}")
        try:
            sender.send_whatsapp_text_to_chat(msg["from"], "معلش، حصلت مشكلة تقنية عندنا. ممكن تبعت رسالتك تاني؟")
        except Exception:
            pass


async def process_message(msg: dict) -> None:
    """Concurrency-safe entry point for one incoming message. A global semaphore
    caps total concurrent LLM-bearing work (protects the Groq/DeepSeek rate
    limits); a per-chat lock preserves the same serial-per-conversation
    guarantee the old poll loop gave by accident, since the agent's own
    max_concurrency=1 assumption depends on only one turn per thread running
    at a time."""
    chat_id = msg["from"]
    lock = _chat_locks.setdefault(chat_id, asyncio.Lock())
    async with _semaphore, lock:
        await asyncio.to_thread(_process_sync, msg)
