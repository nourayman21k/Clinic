from datetime import datetime, timedelta

import psycopg2.errors
from langchain_core.tools import tool

from . import db, sender
from ..config import CLINIC_NAME
from .config import BOOKING_WINDOW_DAYS, CAIRO, CLINIC_HOURS, CLINIC_MAPS_LINK


def _parse_id(value: str, field_name: str) -> tuple:
    """Safely parse a tool-provided id. Returns (int_value, None) on success or
    (None, error_message) on failure -- so a bad/placeholder id from the model
    becomes a normal tool result the agent can react to, instead of crashing
    the whole message pipeline with an uncaught ValueError."""
    try:
        return int(value), None
    except (TypeError, ValueError):
        return None, (
            f"Invalid {field_name}: '{value}' is not a valid numeric id. "
            f"You must use the exact numeric {field_name} returned earlier in this conversation "
            f"by register_patient, lookup_patient, book_appointment, or get_patient_appointments — "
            f"never a placeholder or made-up value. If you don't have it, call the right lookup tool again."
        )


def _date_today():
    return datetime.now(CAIRO).date()


def _validate_booking_datetime(date_str: str, time_str: str) -> tuple:
    """One gate for every write of a slot. Normalizes the time to zero-padded
    HH:MM, validates the format, rejects past date-times, and rejects times
    that aren't real clinic slots. Returns (scheduled_at, None) on success or
    (None, error_message) the agent can react to."""
    try:
        d = datetime.strptime(str(date_str), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None, "Invalid date format. Please ask the patient for a clear date and use YYYY-MM-DD."
    try:
        t = datetime.strptime(str(time_str), "%H:%M").time()
    except (TypeError, ValueError):
        return None, "Invalid time format. Use 24-hour HH:MM, e.g. 09:30 or 14:00."
    time_norm = t.strftime("%H:%M")  # "9:30" -> "09:30"
    if time_norm not in CLINIC_HOURS:
        return None, f"{time_norm} is not a clinic slot. Valid slots: {', '.join(CLINIC_HOURS)}."
    scheduled_dt = datetime.combine(d, t, tzinfo=CAIRO)
    if scheduled_dt < datetime.now(CAIRO):
        return None, f"That date/time ({d.isoformat()} {time_norm}) is in the past. Please ask the patient for an upcoming date."
    max_day = _date_today() + timedelta(days=BOOKING_WINDOW_DAYS)
    if d > max_day:
        return None, (
            f"That date ({d.isoformat()}) is beyond the clinic's booking window. Patients can only book "
            f"within the next {BOOKING_WINDOW_DAYS} days (latest bookable day: {max_day.isoformat()}). "
            f"Politely tell the patient booking only opens two weeks ahead and ask for a nearer date."
        )
    return scheduled_dt, None  # tz-aware datetime -- psycopg2 stores it as correct timestamptz


def _record_exists(table: str, record_id: int) -> bool:
    cur = db.conn.cursor()
    cur.execute(f"SELECT 1 FROM {table} WHERE id = %s", (record_id,))
    return cur.fetchone() is not None


@tool
def lookup_patient(phone: str) -> str:
    """Look up a patient by phone number. Returns patient info if found, or a not-found message."""
    phone = sender.normalize_eg_phone(phone)
    cur = db.conn.cursor()
    cur.execute("SELECT id, name FROM patients WHERE phone = %s", (phone,))
    row = cur.fetchone()
    if row:
        return f"Found patient: patient_id={row[0]}, name={row[1]}"
    return "No patient found with this phone number."


@tool
def register_patient(name: str, phone: str) -> str:
    """Register a new patient. Use this only after confirming the patient is not already in the system.
    Returns the new patient's id -- use that exact patient_id for any follow-up tool call
    (get_available_slots doesn't need it, but book_appointment, get_patient_appointments,
    reschedule_appointment, cancel_appointment, and send_whatsapp_confirmation all do)."""
    phone = sender.normalize_eg_phone(phone)
    cur = db.conn.cursor()
    try:
        cur.execute(
            "INSERT INTO patients (name, phone) VALUES (%s, %s) RETURNING id",
            (name, phone),
        )
        new_patient_id = cur.fetchone()[0]
        return f"Registered new patient '{name}' successfully. patient_id={new_patient_id}, phone={phone}. Use this exact patient_id for any later tool call in this conversation."
    except psycopg2.errors.UniqueViolation:
        # Patient already exists (race or duplicate) -- look them up so the agent still
        # gets a real id. autocommit means the failed INSERT didn't poison anything.
        cur.execute("SELECT id FROM patients WHERE phone = %s", (phone,))
        existing = cur.fetchone()
        if existing:
            return f"A patient with this phone number already exists. patient_id={existing[0]}. Use this exact patient_id."
        return "A patient with this phone number already exists."


@tool
def get_available_slots(doctor_id: str, date: str) -> str:
    """Get available appointment slots for a doctor on a given date (format: YYYY-MM-DD)."""
    try:
        requested_date = datetime.strptime(date, "%Y-%m-%d").date()
    except ValueError:
        return "Invalid date format. Please ask the patient for a clear date and use YYYY-MM-DD."

    if requested_date < _date_today():
        return f"That date ({date}) is in the past. Please ask the patient for a valid upcoming date."

    max_day = _date_today() + timedelta(days=BOOKING_WINDOW_DAYS)
    if requested_date > max_day:
        return (f"That date ({date}) is beyond the clinic's booking window. Patients can only book "
                f"within the next {BOOKING_WINDOW_DAYS} days (latest bookable day: {max_day.isoformat()}). "
                f"Politely tell the patient booking only opens two weeks ahead and ask for a nearer date.")

    doctor_id_int, err = _parse_id(doctor_id, "doctor_id")
    if err:
        return err
    if not _record_exists("doctors", doctor_id_int):
        return f"No doctor with doctor_id={doctor_id_int} exists."

    cur = db.conn.cursor()
    # timestamptz comes back as tz-aware datetimes; compare on the Cairo-local date
    # (the same conversion the dashboard's "today" queries use).
    cur.execute("""
        SELECT scheduled_at FROM appointments
        WHERE doctor_id = %s
          AND (scheduled_at AT TIME ZONE 'Africa/Cairo')::date = %s
          AND status != 'cancelled'
    """, (doctor_id_int, requested_date))
    booked = {row[0].astimezone(CAIRO).strftime("%H:%M") for row in cur.fetchall()}

    available = [t for t in CLINIC_HOURS if t not in booked]
    if requested_date == _date_today():
        now_hm = datetime.now(CAIRO).strftime("%H:%M")
        available = [t for t in available if t > now_hm]

    if not available:
        return f"No available slots for doctor {doctor_id_int} on {date}."
    return f"Available slots on {date}: {', '.join(available)}"


CONSULTATION_FEE_PROCEDURE_NAME = "كشف"


@tool
def get_pending_treatments(patient_id: str) -> str:
    """List a patient's PENDING treatment plan items (procedure + tooth area),
    with their ids. Call this before booking a 'treatment' appointment so the
    patient can pick which one they're coming in for -- use the returned
    treatment_item_id with book_appointment(appointment_type='treatment', treatment_item_id=...)."""
    patient_id_int, err = _parse_id(patient_id, "patient_id")
    if err:
        return err

    cur = db.conn.cursor()
    cur.execute("""
        SELECT t.id, p.name, t.tooth_area
        FROM treatment_items t JOIN procedures p ON t.procedure_id = p.id
        WHERE t.patient_id = %s AND t.status = 'pending'
        ORDER BY t.created_at ASC
    """, (patient_id_int,))
    rows = cur.fetchall()
    if not rows:
        return "This patient has no pending treatment items."
    lines = [
        f"treatment_item_id={r[0]}, procedure={r[1]}" + (f", tooth_area={r[2]}" if r[2] else "")
        for r in rows
    ]
    return "Pending treatment items:\n" + "\n".join(lines)


@tool
def update_patient_name(patient_id: str, new_name: str) -> str:
    """Correct an existing patient's stored name (e.g. they say the name on
    file is wrong/misheard). Use the exact patient_id already established in
    this conversation -- never guess one."""
    patient_id_int, err = _parse_id(patient_id, "patient_id")
    if err:
        return err
    if not _record_exists("patients", patient_id_int):
        return f"No patient with patient_id={patient_id_int} exists."
    if not new_name or not new_name.strip():
        return "new_name cannot be empty."

    cur = db.conn.cursor()
    cur.execute("UPDATE patients SET name = %s WHERE id = %s", (new_name.strip(), patient_id_int))
    return f"Patient {patient_id_int}'s name updated to '{new_name.strip()}'."


@tool
def book_appointment(patient_id: str, doctor_id: str, date: str, time: str,
                      appointment_type: str = "consultation", treatment_item_id: str | None = None) -> str:
    """Book an appointment. date format: YYYY-MM-DD, time format: HH:MM (must be one of the available slots).
    appointment_type is 'consultation' (first visit / check-up -- the default) or 'treatment'
    (an existing pending treatment item, from get_pending_treatments -- pass its treatment_item_id).
    A 'consultation' booking automatically creates the clinic's consultation-fee payment, due before
    the visit -- tell the patient the fee amount from this tool's result.
    Returns the new appointment's id — use that exact id for any follow-up tool call like send_whatsapp_confirmation."""
    patient_id_int, err = _parse_id(patient_id, "patient_id")
    if err:
        return err
    doctor_id_int, err = _parse_id(doctor_id, "doctor_id")
    if err:
        return err
    if appointment_type not in ("consultation", "treatment"):
        return "Invalid appointment_type. Must be 'consultation' or 'treatment'."

    if not _record_exists("patients", patient_id_int):
        return f"No patient with patient_id={patient_id_int} exists. Call lookup_patient or register_patient first and use the id it returns."
    if not _record_exists("doctors", doctor_id_int):
        return f"No doctor with doctor_id={doctor_id_int} exists."

    treatment_item_id_int = None
    if appointment_type == "treatment":
        if treatment_item_id is None:
            return "appointment_type='treatment' requires a treatment_item_id -- call get_pending_treatments first and let the patient pick one."
        treatment_item_id_int, err = _parse_id(treatment_item_id, "treatment_item_id")
        if err:
            return err
        cur = db.conn.cursor()
        cur.execute(
            "SELECT patient_id, status FROM treatment_items WHERE id = %s",
            (treatment_item_id_int,),
        )
        row = cur.fetchone()
        if not row:
            return f"No treatment item with treatment_item_id={treatment_item_id_int} exists."
        if row[0] != patient_id_int:
            return "That treatment item does not belong to this patient."
        if row[1] != "pending":
            return f"That treatment item is already {row[1]}, not pending -- call get_pending_treatments again for the current list."

    scheduled_at, err = _validate_booking_datetime(date, time)
    if err:
        return err

    cur = db.conn.cursor()
    cur.execute("""
        SELECT id FROM appointments
        WHERE doctor_id = %s AND scheduled_at = %s AND status != 'cancelled'
    """, (doctor_id_int, scheduled_at))
    if cur.fetchone():
        return "That slot was just taken. Please choose another time."
    # The SELECT above can still race -- the unique index is the real guard.
    try:
        cur.execute("""
            INSERT INTO appointments (patient_id, doctor_id, scheduled_at, status, appointment_type, treatment_item_id)
            VALUES (%s, %s, %s, 'confirmed', %s, %s) RETURNING id
        """, (patient_id_int, doctor_id_int, scheduled_at, appointment_type, treatment_item_id_int))
        new_appointment_id = cur.fetchone()[0]
    except psycopg2.errors.UniqueViolation:
        return "That slot was just taken. Please choose another time."
    norm_time = scheduled_at.strftime("%H:%M")  # echo the NORMALIZED time back to the agent

    fee_note = ""
    if appointment_type == "consultation":
        cur.execute("SELECT base_price FROM procedures WHERE name = %s", (CONSULTATION_FEE_PROCEDURE_NAME,))
        fee_row = cur.fetchone()
        fee = float(fee_row[0]) if fee_row else 0.0
        cur.execute("""
            INSERT INTO payments (patient_id, appointment_id, base_amount, final_amount, status)
            VALUES (%s, %s, %s, %s, 'pending')
        """, (patient_id_int, new_appointment_id, fee, fee))
        fee_note = f" A consultation fee of {fee:g} EGP is due at the clinic before the visit -- tell the patient this amount."

    return f"Appointment booked successfully. appointment_id={new_appointment_id}, patient_id={patient_id_int}, doctor_id={doctor_id_int}, date={date}, time={norm_time}, appointment_type={appointment_type}.{fee_note}"


@tool
def get_patient_appointments(patient_id: str) -> str:
    """Get all upcoming (non-cancelled) appointments for a patient, with their ids,
    so the agent can identify which one to reschedule or cancel."""
    patient_id_int, err = _parse_id(patient_id, "patient_id")
    if err:
        return err

    cur = db.conn.cursor()
    cur.execute("""
        SELECT id, scheduled_at, status
        FROM appointments
        WHERE patient_id = %s AND status != 'cancelled'
        ORDER BY scheduled_at ASC
    """, (patient_id_int,))
    rows = cur.fetchall()
    if not rows:
        return "This patient has no upcoming appointments."
    # timestamptz arrives in UTC -- always show the patient Cairo wall-clock time
    lines = [
        f"appointment_id={r[0]}, date_time={r[1].astimezone(CAIRO).strftime('%Y-%m-%d %H:%M')}, status={r[2]}"
        for r in rows
    ]
    return "Upcoming appointments:\n" + "\n".join(lines)


def _load_owned_appointment(appointment_id: str, patient_id: str) -> tuple:
    """Shared loader that enforces ownership -- a patient (or a hallucinated id)
    can never cancel/reschedule someone else's appointment.
    Returns (row_dict, None) or (None, error_message)."""
    appointment_id_int, err = _parse_id(appointment_id, "appointment_id")
    if err:
        return None, err
    patient_id_int, err = _parse_id(patient_id, "patient_id")
    if err:
        return None, err

    cur = db.conn.cursor()
    cur.execute("""
        SELECT a.id, a.patient_id, a.doctor_id, a.status, a.scheduled_at,
               p.name, p.phone
        FROM appointments a JOIN patients p ON a.patient_id = p.id
        WHERE a.id = %s
    """, (appointment_id_int,))
    row = cur.fetchone()
    if not row:
        return None, "No appointment found with this id."
    if row[1] != patient_id_int:
        return None, (
            f"appointment_id={appointment_id_int} does not belong to patient_id={patient_id_int}. "
            f"Call get_patient_appointments with the correct patient_id and use one of the ids it returns."
        )
    return {
        "id": row[0], "patient_id": row[1], "doctor_id": row[2],
        "status": row[3], "scheduled_at": row[4], "name": row[5], "phone": row[6],
    }, None


@tool
def cancel_appointment(patient_id: str, appointment_id: str) -> str:
    """Cancel an existing appointment. Requires BOTH the patient_id (from lookup_patient/register_patient)
    and the appointment_id (from get_patient_appointments/book_appointment) — the appointment must belong to that patient.
    Automatically sends a WhatsApp notification to the patient — no need to call any other tool after this."""
    appt, err = _load_owned_appointment(appointment_id, patient_id)
    if err:
        return err
    if appt["status"] == "cancelled":
        return "This appointment is already cancelled."

    cur = db.conn.cursor()
    cur.execute("UPDATE appointments SET status = 'cancelled' WHERE id = %s", (appt["id"],))

    target = sender.get_whatsapp_target(appt["patient_id"], fallback_phone=appt["phone"])
    when = appt["scheduled_at"].astimezone(CAIRO).strftime("%Y-%m-%d %H:%M")
    sent = sender.send_whatsapp_text_to_chat(target, f"Hi {appt['name']}, your appointment on {when} has been cancelled.")
    note = "WhatsApp notification sent." if sent else "WhatsApp notification failed to send."
    return f"Appointment {appt['id']} has been cancelled. {note}"


@tool
def reschedule_appointment(patient_id: str, appointment_id: str, new_date: str, new_time: str) -> str:
    """Reschedule an existing appointment. Requires BOTH the patient_id and the appointment_id —
    the appointment must belong to that patient. new_date format: YYYY-MM-DD, new_time format: HH:MM.
    Automatically sends a WhatsApp notification to the patient — no need to call any other tool after this."""
    appt, err = _load_owned_appointment(appointment_id, patient_id)
    if err:
        return err
    if appt["status"] == "cancelled":
        return "Can't reschedule a cancelled appointment. Please book a new one."

    new_scheduled_at, err = _validate_booking_datetime(new_date, new_time)
    if err:
        return err

    cur = db.conn.cursor()
    cur.execute("""
        SELECT id FROM appointments
        WHERE doctor_id = %s AND scheduled_at = %s AND status != 'cancelled' AND id != %s
    """, (appt["doctor_id"], new_scheduled_at, appt["id"]))
    if cur.fetchone():
        return "That new slot is already taken. Please choose another time."

    try:
        cur.execute("UPDATE appointments SET scheduled_at = %s WHERE id = %s", (new_scheduled_at, appt["id"]))
    except psycopg2.errors.UniqueViolation:  # unique index guards the race here too
        return "That new slot is already taken. Please choose another time."

    target = sender.get_whatsapp_target(appt["patient_id"], fallback_phone=appt["phone"])
    sent = sender.send_whatsapp_text_to_chat(target, f"Hi {appt['name']}, your appointment has been moved to {new_date} at {new_time}.")
    note = "WhatsApp notification sent." if sent else "WhatsApp notification failed to send."
    return f"Appointment {appt['id']} rescheduled to {new_date} at {new_time}. {note}"


@tool
def send_whatsapp_confirmation(patient_id: str, appointment_id: str) -> str:
    """Send a WhatsApp booking confirmation and the clinic location to the patient.
    Safe to call multiple times — will not resend if already sent for this appointment."""
    patient_id_int, err = _parse_id(patient_id, "patient_id")
    if err:
        return err
    appointment_id_int, err = _parse_id(appointment_id, "appointment_id")
    if err:
        return err

    cur = db.conn.cursor()
    cur.execute("""
        SELECT p.name, p.phone, a.scheduled_at, a.status, a.confirmation_sent
        FROM appointments a JOIN patients p ON a.patient_id = p.id
        WHERE a.id = %s AND p.id = %s
    """, (appointment_id_int, patient_id_int))
    row = cur.fetchone()
    if not row:
        return "Could not find this appointment/patient to send confirmation."

    name, phone, scheduled_at, status, confirmation_sent = row

    if confirmation_sent:
        return f"Confirmation was already sent for appointment {appointment_id_int}. No need to send again."

    target = sender.get_whatsapp_target(patient_id_int, fallback_phone=phone)
    when = scheduled_at.astimezone(CAIRO).strftime("%Y-%m-%d %H:%M")
    resp1_ok = sender.send_whatsapp_text_to_chat(target, f"Hi {name}, your appointment is confirmed for {when}.")
    resp2_ok = sender.send_whatsapp_text_to_chat(target, f"📍 {CLINIC_NAME} location:\n{CLINIC_MAPS_LINK}")

    if resp1_ok and resp2_ok:
        cur.execute("UPDATE appointments SET confirmation_sent = TRUE WHERE id = %s", (appointment_id_int,))
        return f"Confirmation and location sent to {name} at {phone}."
    elif resp1_ok:
        return "Confirmation sent, but location message failed."
    elif resp2_ok:
        return "Location sent, but confirmation message failed."
    else:
        return "Both WhatsApp sends failed."


tools = [
    lookup_patient, register_patient, get_available_slots, book_appointment,
    reschedule_appointment, cancel_appointment, send_whatsapp_confirmation,
    get_patient_appointments, get_pending_treatments, update_patient_name,
]
