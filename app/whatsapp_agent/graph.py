import re
from datetime import date

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from pydantic import BaseModel, Field
from typing import Annotated, Literal, Optional
from typing_extensions import TypedDict

from . import db, sender
from .llm import llm_full, llm_mini
from .retry import with_retry
from .tools import tools

SYSTEM_PROMPT = """You are a friendly Egyptian clinic voice receptionist.
Speak in Egyptian Arabic dialect with patients.

HARD RULES — never break these:
- NEVER ask the patient for their phone number, and NEVER ask them to confirm/retype/spell it.
  You are always given it automatically at the start of the conversation, in a system note (their
  WhatsApp number) — or, if they're a returning patient, the system note gives you their patient_id
  directly instead. Use whichever one you're given. This is the whole point of booking over
  WhatsApp: the patient never has to state their own number.
- NEVER invent, guess, or make up an id (patient_id, appointment_id) for any tool call, and NEVER
  write a placeholder like '<patient_id>' or '<appointment_id>' as an argument. The ONLY valid id is
  a real number that appeared earlier in this conversation inside a tool result or a system note, e.g.
  "patient_id=7" from register_patient/lookup_patient, or "appointment_id=12" from book_appointment/
  get_patient_appointments. If you don't have that number yet, call the right lookup tool first
  (lookup_patient or get_patient_appointments) instead of calling book_appointment/reschedule_appointment/
  cancel_appointment/send_whatsapp_confirmation.
- NEVER call register_patient if lookup_patient already found the patient, or if the system note
  already gives you a patient_id — use that patient_id directly.
- NEVER call send_whatsapp_confirmation unless book_appointment just succeeded in this conversation.
- NEVER call get_available_slots when the patient has given you NO date at all — ask for one in plain text first.
  WRONG: patient says "I want to book" (no date given) -> you call get_available_slots with today's date or any guessed date.
  RIGHT: patient says "I want to book" (no date given) -> you reply asking "What date would you like?"
- BUT when the patient DOES give a date in any natural form, resolve it YOURSELF silently using the
  current-date note at the end of this prompt — NEVER ask them for the year, never ask them to rephrase
  a date you can resolve:
  * relative words: بكرة = tomorrow, بعد بكرة = day after tomorrow, "الأربع الجاي"/"next Wednesday" =
    the next occurrence of that weekday strictly after today, "الأسبوع الجاي" = same weekday next week.
  * day-month with no year (like "30-8" or "٥ سبتمبر"): resolve to the NEXT future occurrence of that
    day-month (normally this year; next year only around New Year). Never ask for the year.
- Bookings are ONLY accepted within the next 14 days. If the resolved date falls beyond that, do NOT
  call any tool. In ONE message, in Egyptian Arabic: (a) tell them that date is out of our booking
  range — احنا بنحجز في حدود أسبوعين بس من النهارده, (b) tell them the LATEST bookable day (today + 14
  days, computed from the current-date note), and (c) immediately offer to book them a day INSIDE the
  window instead, e.g. "تحب احجزلك يوم ايه قبل كده؟". Never just refuse and stop — always steer them
  to a bookable day.
- cancel_appointment and reschedule_appointment BOTH require the patient_id AND the appointment_id, and the
  appointment must belong to that patient. Always use the patient_id from the system note or lookup_patient,
  and call get_patient_appointments (for the appointment_id) before cancelling or rescheduling.
- When a patient asks to reschedule or cancel without specifying which appointment or giving a new time, call get_patient_appointments FIRST and mention the existing appointment details back to them before asking what they'd like to change.
- If a system note tells you a name was heard from a VOICE message, speech-to-text can mishear names.
  You MUST read the name back to the patient in Egyptian Arabic and ask them to confirm it BEFORE
  calling register_patient — e.g. "سمعتك بتقول اسمك أحمد محمود، صح؟". Wait for their reply. If they
  confirm, use that name. If they correct it (typed or spoken), use the CORRECTED name they just gave,
  never the one you originally heard. A name typed directly by the patient (not from voice) needs no
  confirmation.
- If an EXISTING patient (you already have their patient_id) says their name on file is wrong or asks
  to correct it, call update_patient_name(patient_id, new_name) — never register_patient for someone
  who already has a patient_id.
- Before booking a NEW appointment, determine whether the patient needs a 'consultation' (first visit /
  check-up) or 'treatment' (continuing a specific problem, e.g. "عايز اكمل علاج السنة") — ask if it
  isn't already clear from what they said. If 'treatment', call get_pending_treatments(patient_id) FIRST
  and read the list back to them so they pick exactly ONE item; use that item's treatment_item_id when
  calling book_appointment(appointment_type='treatment', treatment_item_id=...). If get_pending_treatments
  returns none, tell them so and offer a 'consultation' booking instead. Default to appointment_type=
  'consultation' when they clearly mean a first visit/check-up.
- After a 'consultation' booking, book_appointment's result includes a consultation fee — you MUST tell
  the patient this exact amount and that it's payable at the clinic before their visit, in the same
  message as the booking confirmation.

Correct step order for booking:
1. Check the system note at the very start of the conversation.
   - If it says the patient is already known and gives you a patient_id: skip straight to step 3
     using that exact patient_id. Do NOT call lookup_patient or register_patient again.
   - Otherwise it gives you their WhatsApp phone number: call lookup_patient with that exact number
     immediately. Never ask the patient for it, and never wait for them to state it themselves.
2. If lookup_patient does NOT find them: ask ONLY for their name — do NOT ask for phone (you already
   have it from the system note), and do NOT ask for age or gender (we don't collect them). If the name
   came from voice, confirm it per the HARD RULE above before proceeding. Then call
   register_patient(name, phone) — its result includes the real patient_id, use that exact number from
   now on. If FOUND: use the patient_id from lookup_patient's result directly.
3. Ask the patient what they need (booking, reschedule, cancel) and, for a new booking, whether it's a
   consultation or treatment (see HARD RULES above).
4. Ask for the preferred date in plain text. WAIT for their reply. Once they state a date in ANY form
   (relative, day-month, full), resolve it to YYYY-MM-DD yourself per the rules above and call
   get_available_slots for doctor_id=1 with it.
5. Once the patient confirms a specific time, call book_appointment using the real patient_id from step 1/2
   and the appointment_type/treatment_item_id resolved in step 3. Always pass the time zero-padded in
   24-hour HH:MM format (09:30, not 9:30).
6. Only after book_appointment succeeds, call send_whatsapp_confirmation using the real patient_id and the appointment_id book_appointment just returned.
7. Keep responses short and natural, like a real phone conversation.
"""

AR_WEEKDAYS = ["الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"]


class ClinicState(TypedDict):
    messages: Annotated[list, add_messages]
    use_full_model: bool


llm_mini_with_tools = llm_mini.bind_tools(tools)
llm_full_with_tools = llm_full.bind_tools(tools)


def _dated_system_prompt() -> str:
    """The system prompt isn't persisted by the checkpointer (agent() only returns
    the response), so it's rebuilt on every call -- which lets us stamp it with the
    CURRENT date each turn. That's what makes 'بكرة' and 'الأربع الجاي' resolvable,
    keeps the year implicit, and stays correct even if the process runs across
    midnight/new year."""
    from datetime import datetime as _dt
    now = _dt.now()
    return SYSTEM_PROMPT + (
        f"\n\nCurrent-date note: today is {now.strftime('%Y-%m-%d')}, "
        f"a {now.strftime('%A')} (يوم {AR_WEEKDAYS[now.weekday()]}). "
        f"Resolve every relative or year-less date the patient gives against this."
    )


def agent(state: ClinicState) -> ClinicState:
    messages = state["messages"]
    if not any(isinstance(m, SystemMessage) for m in messages):
        messages = [SystemMessage(content=_dated_system_prompt())] + messages
    use_full_model = state.get("use_full_model", False)
    model_to_use = llm_full_with_tools if use_full_model else llm_mini_with_tools
    # Retry a transient LLM failure HERE, inside this single graph.invoke() call,
    # instead of letting it bubble out to the caller -- see retry.with_retry's docstring.
    response = with_retry(lambda: model_to_use.invoke(messages))
    return {"messages": [response]}


_builder = StateGraph(ClinicState)
_builder.add_node("agent", agent)
_builder.add_node("tools", ToolNode(tools))
_builder.add_edge(START, "agent")
_builder.add_conditional_edges("agent", tools_condition)
_builder.add_edge("tools", "agent")

_memory = MemorySaver()
graph = _builder.compile(checkpointer=_memory)


class RouteDecision(BaseModel):
    route: Literal["direct", "agent"] = Field(
        description="'direct' ONLY for greetings, thanks, or pure small talk with zero actionable content. "
                    "'agent' for anything involving booking, rescheduling, cancelling, asking about appointments, or any real request — even simple ones."
    )
    complexity: Literal["simple", "complex"] = Field(
        description="'simple' if a single clear request the small model can handle alone. "
                    "'complex' if it bundles multiple requests, is contradictory, angry/confused, or otherwise needs stronger reasoning."
    )


router_structured = llm_mini.with_structured_output(RouteDecision, method="json_mode")

# Cheap keyword escalation used mid-conversation instead of an LLM call per turn.
ESCALATION_KEYWORDS = [
    "زعلان", "مش عاجبني", "وحش", "سيء", "مش راضي", "هشتكي", "شكوى",
    "عايز حد", "عايزة حد", "بني ادم", "بني آدم", "انسان حقيقي", "إنسان حقيقي",
    "موظف", "مسؤول", "المدير",
]

DIRECT_REPLIES_KEYWORDS_HINT = "شكراً! لو احتجت أي حاجة تانية أنا موجود. 😊"


def route_message(text: str) -> RouteDecision:
    """Wrapped in try/except -- a json_mode failure on the mini model must never
    propagate up and silently drop the patient's message; a router failure just
    escalates safely instead."""
    try:
        return router_structured.invoke(
            f"Classify this patient message for an Egyptian dental clinic assistant. "
            f"Respond with a JSON object matching the required schema.\n"
            f"route='direct' ONLY if it's a greeting, thanks, or pure small talk with no actionable request.\n"
            f"route='agent' for ANY booking, rescheduling, cancelling, question, or real request — even simple ones.\n\n"
            f"complexity='complex' if ANY of these apply:\n"
            f"- the message expresses anger, frustration, or complaint (e.g. 'مش عاجبني', 'زعلان', 'وحش')\n"
            f"- the patient explicitly asks for a human / real person instead of the bot\n"
            f"- the message bundles multiple distinct unrelated requests\n"
            f"- the message is contradictory or confusing to parse\n"
            f"Otherwise complexity='simple', even if it contains multiple pieces of info about ONE single request "
            f"(like a booking with name+date+time all at once — that is still 'simple').\n\n"
            f"Message: {text}"
        )
    except Exception as e:
        print(f"   ⚠️ Router failed ({type(e).__name__}) — defaulting to agent/complex.")
        return RouteDecision(route="agent", complexity="complex")


class BookingExtraction(BaseModel):
    patient_name: Optional[str] = Field(default=None, description="Patient's name, if mentioned")
    date: Optional[str] = Field(default=None, description="Requested date in YYYY-MM-DD format if mentioned and unambiguous, else null")
    time: Optional[str] = Field(default=None, description="Requested time in HH:MM 24-hour format if mentioned, else null")
    doctor_preference: Optional[str] = Field(default=None, description="Doctor's name if mentioned, else null")
    intent: Literal["book", "reschedule", "cancel", "unclear"] = Field(description="What the patient wants to do")


extractor_structured = llm_mini.with_structured_output(BookingExtraction)


def extract_booking_info(text: str) -> BookingExtraction:
    today = date.today()
    weekday_ar = AR_WEEKDAYS[today.weekday()]
    try:
        return extractor_structured.invoke(
            f"Extract booking details from this Egyptian Arabic patient message for a dental clinic.\n"
            f"Today is {today.isoformat()}, a {today.strftime('%A')} (يوم {weekday_ar}).\n"
            f"Resolve relative dates yourself: بكرة = tomorrow, بعد بكرة = +2 days, "
            f"'الأربع الجاي'/'next Wednesday' = the next occurrence of that weekday after today.\n"
            f"Partial dates (like '30-8' = day 30, month 8) get the current year — but if that "
            f"day-month has already passed this year, use next year instead. Never require a year.\n"
            f"If a date or time is not mentioned at all, leave it null — do not guess.\n\n"
            f"Message: {text}"
        )
    except Exception as e:
        print(f"   ⚠️ Extraction failed ({type(e).__name__}), returning empty extraction.")
        return BookingExtraction(intent="unclear")


def handle_whatsapp_message_v2(chat_id: str, phone: str, message_text: str, is_voice: bool = False) -> str:
    """Routes an incoming WhatsApp message. max_concurrency=1 forces LangGraph's
    ToolNode to execute tool calls SEQUENTIALLY -- by default it runs parallel tool
    calls in a thread pool, and multiple threads hitting our single shared psycopg2
    connection would corrupt cursor state."""
    thread_config = {
        "configurable": {"thread_id": f"whatsapp_{chat_id}"},
        "max_concurrency": 1,
    }
    existing_state = graph.get_state(thread_config)
    is_first_message = not existing_state.values.get("messages")

    if is_first_message:
        route_result = route_message(message_text)
        if route_result.route == "direct":
            reply_text = DIRECT_REPLIES_KEYWORDS_HINT
            sender.send_whatsapp_text_to_chat(chat_id, reply_text)
            return reply_text
        use_full = route_result.complexity == "complex"

        extracted = extract_booking_info(message_text)

        # Look the patient up ourselves, deterministically, instead of just handing
        # the LLM a phone number and trusting it to call lookup_patient with it.
        # The model has been seen second-guessing an @lid-derived number that
        # doesn't look like a real phone and asking the patient for one anyway --
        # exactly what we don't want. This also recognizes a RETURNING patient
        # even on a brand new conversation thread.
        cur = db.conn.cursor()
        cur.execute(
            "SELECT id, name FROM patients WHERE phone = %s OR whatsapp_chat_id = %s",
            (phone, chat_id),
        )
        existing_patient = cur.fetchone()

        if existing_patient:
            patient_id, patient_name = existing_patient
            known_facts = [
                f"(معلومة نظام تلقائية: المريض ده معروف بالفعل عندنا — patient_id={patient_id}، "
                f"الاسم: {patient_name}. متسألوش عن اسمه أو رقمه، ولا تستخدم lookup_patient أو "
                f"register_patient تاني — استخدم patient_id={patient_id} ده على طول في أي تول محتاجه.)"
            ]
        else:
            known_facts = [
                f"(معلومة نظام تلقائية — متسألش المريض عنها أبداً: رقم واتساب المرسل هو {phone}. "
                f"استخدمه مباشرة كـ phone في lookup_patient أو register_patient من غير ما تطلب من "
                f"المريض يقول رقمه أو يأكده.)"
            ]

        if extracted.patient_name:
            known_facts.append(f"اسم المريض المذكور: {extracted.patient_name}")
            if is_voice and not existing_patient:
                known_facts.append(
                    "(ملحوظة: الاسم ده اتسمع من رسالة صوتية، ومحتمل يكون فيه خطأ في التعرف على الكلام — "
                    "لازم تأكد الاسم مع المريض الأول قبل ما تستخدم register_patient.)"
                )
        if extracted.date:
            known_facts.append(f"التاريخ المطلوب: {extracted.date}")
        if extracted.time:
            known_facts.append(f"الوقت المطلوب: {extracted.time}")
        if extracted.doctor_preference:
            known_facts.append(f"الدكتور المطلوب: {extracted.doctor_preference}")
        enriched_text = "\n".join(known_facts) + f"\n{message_text}"
    else:
        use_full = any(kw in message_text for kw in ESCALATION_KEYWORDS)
        enriched_text = message_text

    result = None
    try:
        result = graph.invoke(
            {"messages": [HumanMessage(content=enriched_text)], "use_full_model": use_full},
            thread_config,
        )
        reply_text = result["messages"][-1].content
    except Exception as e:
        # The dispatcher does not retry a failed message at all (see dispatcher.py) --
        # a transient error just gets the same generic apology as any other failure.
        print(f"   ⚠️ Agent crashed processing message from {chat_id}: {type(e).__name__}: {e}")
        reply_text = "معلش، حصلت مشكلة تقنية عندنا. ممكن تبعت طلبك تاني؟"

    # Link whatsapp_chat_id straight to whichever patient this turn actually
    # touched, read from lookup_patient/register_patient's own result in THIS
    # turn -- instead of only guessing by matching `phone` against patients.phone
    # (that guess silently fails whenever @lid resolution fails).
    if result is not None:
        for m in result["messages"]:
            if getattr(m, "name", None) in ("lookup_patient", "register_patient"):
                match = re.search(r"patient_id=(\d+)", str(m.content))
                if match:
                    cur = db.conn.cursor()
                    cur.execute(
                        """UPDATE patients SET whatsapp_chat_id = %s
                           WHERE id = %s AND (whatsapp_chat_id IS NULL OR whatsapp_chat_id != %s)""",
                        (chat_id, int(match.group(1)), chat_id),
                    )

    # Legacy fallback heal (kept as a safety net for cases the loop above
    # doesn't cover, e.g. no lookup/register tool call happened this turn).
    cur = db.conn.cursor()
    cur.execute(
        """UPDATE patients SET whatsapp_chat_id = %s
           WHERE (phone = %s OR whatsapp_chat_id = %s)
             AND (whatsapp_chat_id IS NULL OR whatsapp_chat_id != %s)""",
        (chat_id, phone, chat_id, chat_id),
    )

    sent = sender.send_whatsapp_text_to_chat(chat_id, reply_text)
    if not sent:
        print(f"WARNING: failed to send WhatsApp reply to {chat_id}")

    return reply_text
