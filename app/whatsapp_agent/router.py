"""System 1 router for the WhatsApp agent -- the idea from github.com/nourayman21k/JEV.

A local open-weights model (open-alternative-jev's `so1.Decider` over
Qwen/Qwen3.5-4B, the same model JEV uses) reads each patient message in ONE
forward pass -- no text generated -- and returns which tool it needs, with a
confidence. The agent then binds only that tool group instead of sending all 10
tool schemas to Groq on every call; JEV measured 51% fewer tokens that way.

JEV's three outcomes:
  confident tool label (>= CONFIDENCE_THRESHOLD) -> that tool + the lookups it depends on (TOOL_GROUPS)
  confident "general_chat"                       -> only the identity lookups (GENERAL_CHAT_TOOLS) --
                                                    JEV bound none, which breaks this agent's prompt
  anything less confident                        -> all tools (the pre-router behavior)

Two adaptations, because WhatsApp conversations are multi-turn (JEV routed
single questions): the router also sees the bot's previous message, so "١٠:٣٠"
after "تحب الساعة كام؟" reads as booking; and a follow-up turn never loses the
tools the previous turn had, so a booking can't fail halfway.

Loaded in 8-bit by default (~4.5 GB instead of ~8.4 GB) so it fits on the same
12 GB GPU as the speech model. If the package, the GPU or the model is
unavailable, the router disables itself and every message gets all tools --
the agent never breaks because of it.
"""
import os
import threading
import time
from functools import lru_cache

from langsmith import traceable

from .tools import tools

ENABLED = os.getenv("WHATSAPP_AGENT_USE_JEV", "1") != "0"
JEV_MODEL = os.getenv("JEV_MODEL", "Qwen/Qwen3.5-4B")
JEV_QUANTIZATION = os.getenv("JEV_QUANTIZATION", "8bit")  # 8bit | 4bit | bf16
CONFIDENCE_THRESHOLD = float(os.getenv("JEV_CONFIDENCE_THRESHOLD", "0.75"))
# Binding NO tools is the costliest mistake (the agent can't act at all that turn),
# so "general_chat" needs a stricter bar. In the benchmark every real greeting/thanks
# scored >= 0.997, while the one question misread as small talk scored 0.84.
GENERAL_CHAT_THRESHOLD = float(os.getenv("JEV_GENERAL_CHAT_THRESHOLD", "0.95"))

ALL_TOOLS = [t.name for t in tools]
TOOL_LABELS = ALL_TOOLS + ["general_chat"]


def _short(description: str) -> str:
    return " ".join(description.split())[:240]


TOOL_DESCRIPTIONS = "\n".join(f"- {t.name}: {_short(t.description)}" for t in tools) + (
    "\n- general_chat: greetings, thanks, small talk, or a question the assistant can answer "
    "without looking anything up or changing any booking."
)

# "general_chat" binds just the patient-identity lookups, never zero tools: this
# agent's prompt always starts by identifying the patient (call lookup_patient),
# and with NO tools bound gpt-oss on Groq still tries to -- verified end to end,
# it then returns an empty reply or fails with "Tool choice is none, but model
# called a tool". With these two it behaves exactly like the all-tools baseline.
GENERAL_CHAT_TOOLS = ["lookup_patient", "register_patient"]

# A write tool needs ids only the read tools can find, so each label maps to the
# tools it depends on -- binding the write tool alone would strand the agent.
TOOL_GROUPS = {
    "book_appointment": ["lookup_patient", "register_patient", "get_available_slots", "get_pending_treatments",
                         "book_appointment", "send_whatsapp_confirmation"],
    "get_available_slots": ["get_available_slots"],
    "reschedule_appointment": ["lookup_patient", "get_patient_appointments", "get_available_slots",
                               "reschedule_appointment"],
    "cancel_appointment": ["lookup_patient", "get_patient_appointments", "cancel_appointment"],
    "get_patient_appointments": ["lookup_patient", "get_patient_appointments"],
    # Listing pending treatments is how a treatment booking STARTS (see the agent's
    # prompt), so this group can also finish the booking.
    "get_pending_treatments": ["lookup_patient", "get_pending_treatments", "get_available_slots", "book_appointment",
                               "send_whatsapp_confirmation"],
    "register_patient": ["lookup_patient", "register_patient"],
    "update_patient_name": ["lookup_patient", "update_patient_name"],
    "lookup_patient": ["lookup_patient"],
    "send_whatsapp_confirmation": ["get_patient_appointments", "send_whatsapp_confirmation"],
}

_load_lock = threading.Lock()
_infer_lock = threading.Lock()  # one GPU forward at a time -- the dispatcher runs 5 chats concurrently
_decider = None
_load_error = None


def _load_kwargs() -> dict:
    if JEV_QUANTIZATION == "4bit":
        return {"load_in_4bit": True}
    if JEV_QUANTIZATION == "bf16":
        import torch

        return {"dtype": torch.bfloat16}
    return {"load_in_8bit": True}


def get_decider():
    """The loaded Decider, or None if the router is disabled or failed to load
    (logged once; the agent then binds all tools)."""
    global _decider, _load_error
    if not ENABLED or _decider is not None or _load_error is not None:
        return _decider
    with _load_lock:
        if _decider is None and _load_error is None:
            try:
                from so1 import Decider

                started = time.time()
                _decider = Decider.from_pretrained(JEV_MODEL, backend="hf", mode="separate", **_load_kwargs())
                print(f"[jev] Router ready: {JEV_MODEL} ({JEV_QUANTIZATION}) in {time.time() - started:.0f}s.")
            except Exception as e:
                _load_error = f"{type(e).__name__}: {e}"
                print(f"[jev] ⚠️ Router disabled -- could not load {JEV_MODEL}: {_load_error}. "
                      f"Every message gets all tools (the pre-router behavior).")
    return _decider


def warm_up() -> None:
    """Load the model at startup (background thread) so the first patient doesn't wait for it."""
    get_decider()


def _state(user_message: str, previous_reply: str | None) -> str:
    context = f"Assistant's previous message: {previous_reply[:600]}\n" if previous_reply else ""
    return (f"A dental clinic's WhatsApp assistant can use these tools:\n{TOOL_DESCRIPTIONS}\n\n"
            f"{context}Patient's new message: {user_message}")


@traceable(name="jev_router", run_type="chain")
def classify_with_jev(user_message: str, previous_reply: str | None = None) -> dict | None:
    """{"label", "confidence"} from the local model, or None if it isn't available."""
    decider = get_decider()
    if decider is None:
        return None
    from so1 import Choice

    with _infer_lock:
        decision = decider.decide(
            state=_state(user_message, previous_reply),
            questions=[Choice("Which tool does the assistant need to handle the patient's new message?", TOOL_LABELS)],
            mode="separate",
        )[0]
    return {"label": decision.choice, "confidence": float(decision.confidence)}


@lru_cache(maxsize=256)  # deterministic -- lets the first-message check and the graph share one forward pass
def _classify_cached(user_message: str, previous_reply: str | None) -> tuple | None:
    result = classify_with_jev(user_message, previous_reply)
    return (result["label"], result["confidence"]) if result else None


def route(user_message: str, previous_reply: str | None = None, previous_tools: tuple | None = None) -> dict:
    """The routing decision for one patient message.

    previous_tools: the previous turn's tool scope in this conversation -- a tuple
    of names (tuple(ALL_TOOLS) when that turn had every tool), () when it had
    none, or None for a brand-new conversation. A follow-up's scope is always a
    superset of it, so a conversation can only widen what it may do, never lose
    a tool halfway through a booking.

    Returns selected_tools ([] with general_chat False means ALL tools),
    general_chat, the raw jev_label / jev_confidence, and jev_used."""
    everything = {"selected_tools": [], "general_chat": False, "jev_label": "", "jev_confidence": 0.0,
                  "jev_used": False}
    if not ENABLED:
        return everything
    try:
        result = _classify_cached(user_message, previous_reply)
    except Exception as e:
        print(f"[jev] Routing failed ({type(e).__name__}: {e}) -- using all tools for this message.")
        return everything
    if result is None:
        return everything

    label, confidence = result
    raw = {"jev_label": label, "jev_confidence": confidence, "jev_used": True}
    if confidence < CONFIDENCE_THRESHOLD or (label == "general_chat" and confidence < GENERAL_CHAT_THRESHOLD):
        return {**everything, **raw}
    if label == "general_chat":
        if previous_tools:  # mid-conversation: keep what the last turn could do
            return {"selected_tools": list(previous_tools), "general_chat": False, **raw}
        return {"selected_tools": list(GENERAL_CHAT_TOOLS), "general_chat": True, **raw}
    group = TOOL_GROUPS.get(label, [label])
    if previous_tools:
        group = list(dict.fromkeys([*previous_tools, *group]))
    return {"selected_tools": group, "general_chat": False, **raw}
