from . import sender
from .graph import handle_whatsapp_message_v2
from .transcription import save_and_transcribe_voice


def get_message_text(msg: dict) -> str | None:
    """Takes a normalized OpenWA message (text or voice) and returns plain text
    either way. Returns None if the message type isn't supported OR the voice
    media isn't available (so the caller can reply asking the patient to type
    instead)."""
    msg_type = msg.get("type")

    if msg_type == "voice":
        media = msg.get("media") or {}
        if not media.get("data"):
            print(f"WARNING: voice message from {msg.get('from')} has no downloadable media "
                  f"(OpenWA couldn't fetch the audio) -- will ask them to type.")
            return None
        text = save_and_transcribe_voice(media["data"], media["mimetype"])
        print(f"   🎙️ Transcribed voice from {msg.get('from')}: {text}")
        return text

    elif msg_type == "text":
        return msg.get("body", "")

    else:
        print(f"WARNING: unsupported message type '{msg_type}' from {msg.get('from')}")
        return None


def handle_incoming_openwa_message(msg: dict) -> str | None:
    """Takes one normalized OpenWA message (text or voice) and routes it through
    the booking agent. Each distinct WhatsApp chat_id gets its own conversation
    thread/memory automatically -- handle_whatsapp_message_v2 keys the LangGraph
    checkpointer by f"whatsapp_{chat_id}", so patient A's conversation state
    never leaks into patient B's."""
    chat_id = msg["from"]  # real chat_id from WhatsApp -- never rebuild it

    # For @lid contacts, the part before '@' is a privacy LID, NOT a phone
    # number -- resolve the real number first, or a duplicate patient gets
    # registered under the LID's digits.
    if chat_id.endswith("@lid"):
        resolved = sender.resolve_lid_to_real_jid(chat_id)
        phone = resolved.split("@")[0]
        if resolved == chat_id:
            print(f"   ⚠️ Could not resolve real phone for {chat_id} — "
                  f"the agent will treat '{phone}' as the phone; expect a possible duplicate patient.")
    else:
        phone = chat_id.split("@")[0]

    message_text = get_message_text(msg)
    if message_text is None:
        sender.send_whatsapp_text_to_chat(chat_id, "معلش، مقدرتش أفهم الرسالة دي. ممكن تكتبها أو تبعتها صوت؟")
        return None

    return handle_whatsapp_message_v2(chat_id, phone, message_text, is_voice=(msg.get("type") == "voice"))
