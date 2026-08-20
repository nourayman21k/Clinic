# agent_core.py
import os
import sqlite3
from typing import Union
from dotenv import load_dotenv
import requests

from langchain_core.tools import tool
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_groq import ChatGroq
from langgraph.graph import StateGraph, MessagesState, START, END
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.checkpoint.memory import MemorySaver
from datetime import datetime, date as date_cls

load_dotenv()
os.environ["GROQ_API_KEY"] = os.getenv("GROQ_API_KEY")

conn = sqlite3.connect("clinic.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS patients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    phone TEXT UNIQUE NOT NULL,
    age INTEGER,
    gender TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS doctors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    specialty TEXT,
    is_active BOOLEAN DEFAULT 1
)
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS appointments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id INTEGER REFERENCES patients(id),
    doctor_id INTEGER REFERENCES doctors(id),
    scheduled_at TIMESTAMP NOT NULL,
    status TEXT DEFAULT 'confirmed',
    confirmation_sent BOOLEAN DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")
conn.commit()

CLINIC_HOURS = ["9:00","9:30","10:00","10:30","11:00","11:30","12:00","12:30",
                "13:00","13:30","14:00","14:30","15:00","16:00","17:00"]

OPENWA_URL = os.getenv("OPENWA_URL")
OPENWA_API_KEY = os.getenv("OPENWA_API_KEY")
OPENWA_SESSION_ID = os.getenv("OPENWA_SESSION_ID")

CLINIC_LATITUDE = 30.005726365904355
CLINIC_LONGITUDE = 31.468899256922267
CLINIC_NAME = "Egyptian Dental Clinic"
CLINIC_MAPS_LINK = f"https://www.google.com/maps?q={CLINIC_LATITUDE},{CLINIC_LONGITUDE}"

def _send_whatsapp_text(phone: str, text: str) -> bool:
    chat_id = f"{phone}@c.us"
    url = f"{OPENWA_URL}/api/sessions/{OPENWA_SESSION_ID}/messages/send-text"
    headers = {"X-API-Key": OPENWA_API_KEY, "Content-Type": "application/json"}
    resp = requests.post(url, headers=headers, json={"chatId": chat_id, "text": text})
    return resp.ok


@tool
def lookup_patient(phone: str) -> str:
    """Look up a patient by phone number. Returns patient info if found, or a not-found message."""
    cur = conn.cursor()
    cur.execute("SELECT id, name, age, gender FROM patients WHERE phone = ?", (phone,))
    row = cur.fetchone()
    if row:
        return f"Found patient: id={row[0]}, name={row[1]}, age={row[2]}, gender={row[3]}"
    return "No patient found with this phone number."


@tool
def register_patient(name: str, phone: str, age: str, gender: str) -> str:
    """Register a new patient. Use this only after confirming the patient is not already in the system."""
    cur = conn.cursor()
    try:
        age_int = int(age)
    except ValueError:
        return "Invalid age provided — must be a number."
    try:
        cur.execute(
            "INSERT INTO patients (name, phone, age, gender) VALUES (?, ?, ?, ?)",
            (name, phone, age_int, gender)
        )
        conn.commit()
        return f"Registered new patient '{name}' successfully with phone {phone}."
    except sqlite3.IntegrityError:
        return "A patient with this phone number already exists."


@tool
def get_available_slots(doctor_id: str, date: str) -> str:
    """Get available appointment slots for a doctor on a given date (format: YYYY-MM-DD)."""
    try:
        requested_date = datetime.strptime(date, "%Y-%m-%d").date()
    except ValueError:
        return "Invalid date format. Please ask the patient for a clear date and use YYYY-MM-DD."
    if requested_date < date_cls.today():
        return f"That date ({date}) is in the past. Please ask the patient for a valid upcoming date."

    doctor_id_int = int(doctor_id)
    cur = conn.cursor()
    cur.execute("""
        SELECT scheduled_at FROM appointments
        WHERE doctor_id = ? AND date(scheduled_at) = ? AND status != 'cancelled'
    """, (doctor_id_int, date))
    booked = {row[0].split(" ")[1][:5] for row in cur.fetchall()}
    available = [t for t in CLINIC_HOURS if t not in booked]
    if not available:
        return f"No available slots for doctor {doctor_id} on {date}."
    return f"Available slots on {date}: {', '.join(available)}"


@tool
def book_appointment(patient_id: str, doctor_id: str, date: str, time: str) -> str:
    """Book an appointment. date format: YYYY-MM-DD, time format: HH:MM (must be one of the available slots).
    Returns the new appointment's id — use that exact id for any follow-up tool call like send_whatsapp_confirmation."""
    cur = conn.cursor()
    patient_id_int, doctor_id_int = int(patient_id), int(doctor_id)
    scheduled_at = f"{date} {time}:00"
    cur.execute("""
        SELECT id FROM appointments
        WHERE doctor_id = ? AND scheduled_at = ? AND status != 'cancelled'
    """, (doctor_id_int, scheduled_at))
    if cur.fetchone():
        return "That slot was just taken. Please choose another time."
    cur.execute("""
        INSERT INTO appointments (patient_id, doctor_id, scheduled_at, status)
        VALUES (?, ?, ?, 'confirmed')
    """, (patient_id_int, doctor_id_int, scheduled_at))
    conn.commit()
    new_appointment_id = cur.lastrowid
    return f"Appointment booked successfully. appointment_id={new_appointment_id}, patient_id={patient_id}, doctor_id={doctor_id}, date={date}, time={time}."


@tool
def get_patient_appointments(patient_id: str) -> str:
    """Get all upcoming (non-cancelled) appointments for a patient, with their ids,
    so the agent can identify which one to reschedule or cancel."""
    cur = conn.cursor()
    patient_id_int = int(patient_id)
    cur.execute("""
        SELECT id, scheduled_at, status
        FROM appointments
        WHERE patient_id = ? AND status != 'cancelled'
        ORDER BY scheduled_at ASC
    """, (patient_id_int,))
    rows = cur.fetchall()
    if not rows:
        return "This patient has no upcoming appointments."
    lines = [f"appointment_id={r[0]}, date_time={r[1]}, status={r[2]}" for r in rows]
    return "Upcoming appointments:\n" + "\n".join(lines)


@tool
def cancel_appointment(appointment_id: str) -> str:
    """Cancel an existing appointment by its id.
    Automatically sends a WhatsApp notification to the patient — no need to call any other tool after this."""
    cur = conn.cursor()
    appointment_id_int = int(appointment_id)
    cur.execute("""
        SELECT a.status, a.scheduled_at, p.name, p.phone
        FROM appointments a JOIN patients p ON a.patient_id = p.id
        WHERE a.id = ?
    """, (appointment_id_int,))
    row = cur.fetchone()
    if not row:
        return "No appointment found with this id."
    status, scheduled_at, name, phone = row
    if status == "cancelled":
        return "This appointment is already cancelled."

    cur.execute("UPDATE appointments SET status = 'cancelled' WHERE id = ?", (appointment_id_int,))
    conn.commit()
    sent = _send_whatsapp_text(phone, f"Hi {name}, your appointment on {scheduled_at} has been cancelled.")
    note = "WhatsApp notification sent." if sent else "WhatsApp notification failed to send."
    return f"Appointment {appointment_id} has been cancelled. {note}"


@tool
def reschedule_appointment(appointment_id: str, new_date: str, new_time: str) -> str:
    """Reschedule an existing appointment. new_date format: YYYY-MM-DD, new_time format: HH:MM.
    Automatically sends a WhatsApp notification to the patient — no need to call any other tool after this."""
    cur = conn.cursor()
    appointment_id_int = int(appointment_id)
    cur.execute("""
        SELECT a.doctor_id, a.status, a.scheduled_at, p.name, p.phone
        FROM appointments a JOIN patients p ON a.patient_id = p.id
        WHERE a.id = ?
    """, (appointment_id_int,))
    row = cur.fetchone()
    if not row:
        return "No appointment found with this id."
    doctor_id, status, old_scheduled_at, name, phone = row
    if status == "cancelled":
        return "Can't reschedule a cancelled appointment. Please book a new one."

    new_scheduled_at = f"{new_date} {new_time}:00"
    cur.execute("""
        SELECT id FROM appointments
        WHERE doctor_id = ? AND scheduled_at = ? AND status != 'cancelled' AND id != ?
    """, (doctor_id, new_scheduled_at, appointment_id_int))
    if cur.fetchone():
        return "That new slot is already taken. Please choose another time."

    cur.execute("UPDATE appointments SET scheduled_at = ? WHERE id = ?", (new_scheduled_at, appointment_id_int))
    conn.commit()
    sent = _send_whatsapp_text(phone, f"Hi {name}, your appointment has been moved to {new_date} at {new_time}.")
    note = "WhatsApp notification sent." if sent else "WhatsApp notification failed to send."
    return f"Appointment {appointment_id} rescheduled to {new_date} at {new_time}. {note}"


@tool
def send_whatsapp_confirmation(patient_id: str, appointment_id: str) -> str:
    """Send a WhatsApp booking confirmation and the clinic location to the patient.
    Safe to call multiple times — will not resend if already sent for this appointment."""
    cur = conn.cursor()
    patient_id_int, appointment_id_int = int(patient_id), int(appointment_id)
    cur.execute("""
        SELECT p.name, p.phone, a.scheduled_at, a.status, a.confirmation_sent
        FROM appointments a JOIN patients p ON a.patient_id = p.id
        WHERE a.id = ? AND p.id = ?
    """, (appointment_id_int, patient_id_int))
    row = cur.fetchone()
    if not row:
        return "Could not find this appointment/patient to send confirmation."
    name, phone, scheduled_at, status, confirmation_sent = row
    if confirmation_sent:
        return f"Confirmation was already sent for appointment {appointment_id}. No need to send again."

    chat_id = f"{phone}@c.us"
    url = f"{OPENWA_URL}/api/sessions/{OPENWA_SESSION_ID}/messages/send-text"
    headers = {"X-API-Key": OPENWA_API_KEY, "Content-Type": "application/json"}
    resp1 = requests.post(url, headers=headers, json={"chatId": chat_id, "text": f"Hi {name}, your appointment is confirmed for {scheduled_at}."})
    resp2 = requests.post(url, headers=headers, json={"chatId": chat_id, "text": f"📍 {CLINIC_NAME} location:\n{CLINIC_MAPS_LINK}"})

    if resp1.ok and resp2.ok:
        cur.execute("UPDATE appointments SET confirmation_sent = 1 WHERE id = ?", (appointment_id_int,))
        conn.commit()
        return f"Confirmation and location sent to {name} at {phone}."
    elif resp1.ok:
        return f"Confirmation sent, but location failed ({resp2.status_code}): {resp2.text}"
    elif resp2.ok:
        return f"Location sent, but confirmation failed ({resp1.status_code}): {resp1.text}"
    else:
        return f"Both sends failed. Confirmation: {resp1.status_code}, Location: {resp2.status_code}"


tools = [
    lookup_patient, register_patient, get_available_slots, book_appointment,
    reschedule_appointment, cancel_appointment, send_whatsapp_confirmation,
    get_patient_appointments,
]

llm = ChatGroq(model="llama-3.3-70b-versatile", temperature=0)
llm_with_tools = llm.bind_tools(tools)

SYSTEM_PROMPT = """You are a friendly Egyptian clinic voice receptionist.
Speak in Egyptian Arabic dialect with patients.

HARD RULES — never break these:
- NEVER invent, guess, or make up an id (patient_id, appointment_id) for any tool call. Only use an id returned by a previous tool call in this conversation.
- NEVER call register_patient if lookup_patient already found the patient.
- NEVER call send_whatsapp_confirmation unless book_appointment just succeeded in this conversation.
- NEVER call get_available_slots with a guessed or assumed date. If the patient has not explicitly told you a date, your ONLY correct action is to ask them for one in plain text — do not call any tool yet.
- When a patient asks to reschedule or cancel without specifying which appointment or giving a new time, call get_patient_appointments FIRST and mention the existing appointment details back to them before asking what they'd like to change.

Correct step order for booking:
1. Call lookup_patient with the phone number.
2. If NOT found: collect name, age, gender, then call register_patient. If FOUND: use that patient's id directly.
3. Ask the patient what they need (booking, reschedule, cancel).
4. Ask for the preferred date in plain text. WAIT for their reply. Only after they explicitly state a date, call get_available_slots for doctor_id=1 with that date.
5. Once the patient confirms a specific time, call book_appointment.
6. Only after book_appointment succeeds, call send_whatsapp_confirmation.
7. Keep responses short and natural, like a real phone conversation.
"""

def agent(state: MessagesState) -> MessagesState:
    messages = state["messages"]
    if not any(isinstance(m, SystemMessage) for m in messages):
        messages = [SystemMessage(content=SYSTEM_PROMPT)] + messages
    response = llm_with_tools.invoke(messages)
    return {"messages": [response]}

builder = StateGraph(MessagesState)
builder.add_node("agent", agent)
builder.add_node("tools", ToolNode(tools))
builder.add_edge(START, "agent")
builder.add_conditional_edges("agent", tools_condition)
builder.add_edge("tools", "agent")

memory = MemorySaver()
graph = builder.compile(checkpointer=memory)


def get_agent_reply(thread_id: str, user_text: str, sender_phone: str = None) -> str:
    """Call this from the Twilio webhook or the WhatsApp webhook. thread_id should be
    something that keeps each conversation's memory separate (Twilio CallSid, or the
    patient's WhatsApp phone number). If sender_phone is given, it's handed to the agent
    as context so it can use it directly for lookup_patient/register_patient instead of
    asking the patient to repeat their number."""
    config = {"configurable": {"thread_id": thread_id}}
    content = user_text
    if sender_phone:
        content = (
            f"[System note: the patient's WhatsApp phone number is {sender_phone}. "
            f"Use this exact number for lookup_patient/register_patient — do not ask them for it.]\n\n{user_text}"
        )
    result = graph.invoke({"messages": [HumanMessage(content=content)]}, config)
    return result["messages"][-1].content