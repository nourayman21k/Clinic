from datetime import date, datetime, timedelta
from typing import List, Optional
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Appointment, Doctor, Patient, Procedure, Payment, PatientProcedure, TreatmentItem, User
from ..auth import require_doctor
from .. import analytics, reports, voice_charge, voice_treatment_plan

router = APIRouter(prefix="/api/doctor", tags=["doctor"])


class ProcedureLine(BaseModel):
    procedure_id: int
    quantity: int = 1
    tooth_area: str | None = None


class PostChargeRequest(BaseModel):
    patient_id: int
    procedures: List[ProcedureLine]
    notes: str | None = None


class TreatmentPlanLine(BaseModel):
    procedure_id: int
    tooth_area: str | None = None
    notes: str | None = None


class PostTreatmentPlanRequest(BaseModel):
    patient_id: int
    items: List[TreatmentPlanLine]


@router.get("/patients/search")
def search_patients(q: str, db: Session = Depends(get_db), current_user: User = Depends(require_doctor)):
    """Search patients by name or phone — powers the doctor's patient picker."""
    results = db.query(Patient).filter(
        (Patient.name.ilike(f"%{q}%")) | (Patient.phone.ilike(f"%{q}%"))
    ).limit(10).all()
    return [{"id": p.id, "name": p.name, "phone": p.phone} for p in results]


@router.get("/procedures")
def list_procedures(db: Session = Depends(get_db), current_user: User = Depends(require_doctor)):
    """Full procedure catalogue — powers the doctor's procedure picker."""
    procs = db.query(Procedure).all()
    return [{"id": p.id, "name": p.name, "base_price": float(p.base_price), "unit": p.unit} for p in procs]


@router.get("/patients/{patient_id}")
def patient_detail(patient_id: int, db: Session = Depends(get_db), current_user: User = Depends(require_doctor)):
    """Doctor-facing patient history: past appointments (with type/status) and
    what happened at each visit (procedures charged), plus the persistent
    treatment plan -- all sourced straight from the database, never from a
    voice draft. Same response shape as secretary.patient_detail (minus the
    opt-in flag, which is a front-desk concern) so the frontend can reuse one component."""
    patient = db.query(Patient).filter(Patient.id == patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found.")

    appointments = (
        db.query(Appointment)
        .filter(Appointment.patient_id == patient_id)
        .order_by(Appointment.scheduled_at.desc())
        .all()
    )
    payments = (
        db.query(Payment)
        .filter(Payment.patient_id == patient_id)
        .order_by(Payment.created_at.desc())
        .all()
    )
    treatment_items = (
        db.query(TreatmentItem)
        .filter(TreatmentItem.patient_id == patient_id)
        .order_by(TreatmentItem.created_at.desc())
        .all()
    )

    total_spent = sum(float(p.final_amount) for p in payments if p.status == "paid")
    completed_visits = [a for a in appointments if a.status == "done"]
    last_visit = max((a.scheduled_at for a in completed_visits), default=None)

    return {
        "patient_id": patient.id,
        "name": patient.name,
        "phone": patient.phone,
        "age": patient.age,
        "gender": patient.gender,
        "created_at": patient.created_at.isoformat() if patient.created_at else None,
        "total_spent": round(total_spent, 2),
        "last_visit": last_visit.isoformat() if last_visit else None,
        "total_appointments": len(appointments),
        "no_show_count": sum(1 for a in appointments if a.status == "no_show"),
        "appointments": [{
            "appointment_id": a.id,
            "scheduled_at": a.scheduled_at.isoformat(),
            "status": a.status,
            "appointment_type": a.appointment_type,
        } for a in appointments],
        "payments": [{
            "payment_id": p.id,
            "final_amount": float(p.final_amount),
            "status": p.status,
            "method": p.method,
            "paid_at": p.paid_at.isoformat() if p.paid_at else None,
            "created_at": p.created_at.isoformat() if p.created_at else None,
            "procedures": [{
                "procedure_name": pp.procedure.name,
                "quantity": pp.quantity,
                "price_charged": float(pp.price_charged),
                "tooth_area": pp.tooth_area,
            } for pp in p.procedures],
        } for p in payments],
        "treatment_items": [{
            "treatment_item_id": t.id,
            "procedure_name": t.procedure.name,
            "tooth_area": t.tooth_area,
            "status": t.status,
            "notes": t.notes,
            "created_at": t.created_at.isoformat() if t.created_at else None,
            "completed_at": t.completed_at.isoformat() if t.completed_at else None,
        } for t in treatment_items],
    }


@router.get("/patients/{patient_id}/today-appointment")
def patient_today_appointment(patient_id: int, db: Session = Depends(get_db), current_user: User = Depends(require_doctor)):
    """The patient's own still-confirmed appointment scheduled today (Cairo-local),
    if any -- powers the "Complete visit" action in the Charges/Treatment Plan
    tabs, so the doctor can mark today's visit done right after charging/planning
    without switching to the secretary's dashboard. Null when there isn't one."""
    day_cairo = func.date(func.timezone("Africa/Cairo", Appointment.scheduled_at))
    today = func.date(func.timezone("Africa/Cairo", func.now()))
    appt = (
        db.query(Appointment)
        .filter(Appointment.patient_id == patient_id, Appointment.status == "confirmed", day_cairo == today)
        .order_by(Appointment.scheduled_at.asc())
        .first()
    )
    if not appt:
        return None
    return {
        "appointment_id": appt.id,
        "scheduled_at": appt.scheduled_at.isoformat(),
        "appointment_type": appt.appointment_type,
        "status": appt.status,
    }


@router.post("/voice-charge")
def voice_charge_draft(
    audio: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_doctor),
):
    """Transcribe a doctor's voice note and extract a draft charge (patient +
    procedure lines) for the doctor to review and edit before posting via the
    existing /charges endpoint. Read-only — never writes to the database.
    Sync def (not async): does two blocking network calls (Whisper, then a
    Groq chat completion), so FastAPI runs it in its worker thread pool
    instead of blocking the event loop."""
    audio_bytes = audio.file.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Empty audio file.")

    try:
        transcript = voice_charge.transcribe_audio(audio_bytes, audio.filename or "audio.webm")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Transcription failed: {e}")

    if not transcript:
        raise HTTPException(status_code=422, detail="Could not hear anything — please try recording again.")

    try:
        return voice_charge.build_draft(db, transcript)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not process the recording: {e}")


@router.post("/voice-treatment-plan")
def voice_treatment_plan_draft(
    audio: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_doctor),
):
    """Transcribe the doctor's voice note describing a patient's FUTURE treatment
    needs and extract a draft plan (patient + procedure/tooth-area lines) for
    the doctor to review and edit before posting via /treatment-plan.
    Read-only -- never writes to the database. Mirrors /voice-charge exactly."""
    audio_bytes = audio.file.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Empty audio file.")

    try:
        transcript = voice_charge.transcribe_audio(audio_bytes, audio.filename or "audio.webm")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Transcription failed: {e}")

    if not transcript:
        raise HTTPException(status_code=422, detail="Could not hear anything — please try recording again.")

    try:
        return voice_treatment_plan.build_draft(db, transcript)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not process the recording: {e}")


@router.post("/treatment-plan")
def post_treatment_plan(
    body: PostTreatmentPlanRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_doctor),
):
    """Doctor confirms a (possibly voice-drafted) treatment plan -> creates
    persistent, pending TreatmentItem rows the patient can later book against
    over WhatsApp (see whatsapp_agent.tools.get_pending_treatments)."""
    patient = db.query(Patient).filter(Patient.id == body.patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found.")
    if not body.items:
        raise HTTPException(status_code=400, detail="At least one treatment item is required.")

    created = []
    for item in body.items:
        proc = db.query(Procedure).filter(Procedure.id == item.procedure_id).first()
        if not proc:
            raise HTTPException(status_code=404, detail=f"Procedure id {item.procedure_id} not found.")
        row = TreatmentItem(
            patient_id=patient.id,
            procedure_id=proc.id,
            tooth_area=item.tooth_area,
            notes=item.notes,
            status="pending",
        )
        db.add(row)
        created.append(row)

    db.commit()
    for row in created:
        db.refresh(row)

    return {
        "patient_id": patient.id,
        "patient_name": patient.name,
        "items_created": len(created),
        "treatment_item_ids": [r.id for r in created],
        "message": f"Treatment plan saved for {patient.name}: {len(created)} item(s) pending.",
    }


@router.get("/analytics/monthly")
def monthly_analytics(
    year: Optional[int] = None,
    month: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_doctor),
):
    """Read-only monthly revenue/procedure/patient analytics + LLM-written
    Arabic insights. The current month is always computed fresh; a past
    month is served from a cached snapshot (computed once and stored) so it
    doesn't re-run an LLM call on every view. Defaults to the current month.
    Sync def: the insights step is a blocking Groq call, same reasoning as
    voice_charge_draft above."""
    today = date.today()
    year = year or today.year
    month = month or today.month
    if not (1 <= month <= 12):
        raise HTTPException(status_code=400, detail="month must be between 1 and 12.")
    return analytics.get_or_build_monthly_analytics(db, year, month)


@router.get("/analytics/monthly/pdf")
def monthly_analytics_pdf(
    year: Optional[int] = None,
    month: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_doctor),
):
    """Download the same monthly report as a PDF -- the design doc's
    'PDF monthly report for doctor', available on demand here as well as
    auto-emailed by the scheduler's monthly job. Same year/month defaulting
    and snapshot-caching as the JSON endpoint above."""
    today = date.today()
    year = year or today.year
    month = month or today.month
    if not (1 <= month <= 12):
        raise HTTPException(status_code=400, detail="month must be between 1 and 12.")
    data = analytics.get_or_build_monthly_analytics(db, year, month)
    pdf_bytes = reports.generate_monthly_report_pdf(data)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="report_{year}_{month:02d}.pdf"'},
    )


@router.get("/appointments/today")
def my_todays_appointments(
    date_param: Optional[str] = Query(None, alias="date"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_doctor),
):
    """The logged-in doctor's own appointments for a given day (default today,
    Cairo-local) — their patient queue. Optional ?date= (same convention as
    the secretary endpoints) lets the frontend's date-strip navigate days;
    omitted, behavior is identical to before."""
    doctor = db.query(Doctor).filter(Doctor.user_id == current_user.id).first()
    if not doctor:
        raise HTTPException(status_code=404, detail="No doctor profile linked to this account.")

    day_cairo = func.date(func.timezone("Africa/Cairo", Appointment.scheduled_at))
    if date_param:
        try:
            target_date = datetime.strptime(date_param, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=400, detail="date must be in YYYY-MM-DD format.")
    else:
        # Unchanged from before this endpoint took a ?date= param: Cairo-local
        # "today" computed in SQL, not Python's server-local date.today().
        target_date = func.date(func.timezone("Africa/Cairo", func.now()))

    appts = (
        db.query(Appointment)
        .filter(Appointment.doctor_id == doctor.id, day_cairo == target_date, Appointment.status != "cancelled")
        .order_by(Appointment.scheduled_at.asc())
        .all()
    )

    # One query for every linked payment, rather than N+1 per appointment.
    appt_ids = [a.id for a in appts]
    payment_by_appt = {
        p.appointment_id: p.status
        for p in db.query(Payment).filter(Payment.appointment_id.in_(appt_ids)).all()
    } if appt_ids else {}

    return [{
        "appointment_id": a.id,
        "patient_id": a.patient_id,
        "patient_name": a.patient.name,
        "patient_phone": a.patient.phone,
        "scheduled_at": a.scheduled_at.isoformat(),
        "status": a.status,
        "appointment_type": a.appointment_type,
        # null when no payment is linked (e.g. a treatment appointment, or a
        # legacy row from before this feature) -- the frontend only shows the
        # unpaid warning for appointment_type == 'consultation' with a
        # non-'paid' status here.
        "payment_status": payment_by_appt.get(a.id),
    } for a in appts]


@router.get("/visits-trend")
def visits_trend(
    days: int = Query(14, ge=1, le=90),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_doctor),
):
    """Daily visit (non-cancelled appointment) counts for the last `days` days
    (Cairo-local, today inclusive), zero-filled so a trend chart gets one
    point per day even on days with no visits."""
    doctor = db.query(Doctor).filter(Doctor.user_id == current_user.id).first()
    if not doctor:
        raise HTTPException(status_code=404, detail="No doctor profile linked to this account.")

    end_date = date.today()
    start_date = end_date - timedelta(days=days - 1)
    day_cairo = func.date(func.timezone("Africa/Cairo", Appointment.scheduled_at))

    rows = (
        db.query(day_cairo.label("day"), func.count(Appointment.id).label("visits"))
        .filter(
            Appointment.doctor_id == doctor.id,
            Appointment.status != "cancelled",
            day_cairo >= start_date,
            day_cairo <= end_date,
        )
        .group_by(day_cairo)
        .all()
    )
    by_day = {r.day.isoformat(): r.visits for r in rows}
    return [
        {
            "date": (start_date + timedelta(days=n)).isoformat(),
            "visits": by_day.get((start_date + timedelta(days=n)).isoformat(), 0),
        }
        for n in range(days)
    ]


@router.post("/charges")
def post_charge(
    body: PostChargeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_doctor),
):
    """Doctor posts what was done -> server calculates the price -> creates a
    pending payment the secretary will see on their dashboard.

    If a posted line matches one of this patient's PENDING treatment plan
    items (same procedure, same tooth area -- or both general/no tooth area),
    that item is automatically marked completed. This is the "charging it
    IS doing it" path for a treatment the patient was already booked for
    ad-hoc (not through the treatment-appointment flow) -- e.g. the doctor
    treats something during a consultation instead of a dedicated later visit."""
    patient = db.query(Patient).filter(Patient.id == body.patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found.")
    if not body.procedures:
        raise HTTPException(status_code=400, detail="At least one procedure is required.")

    line_items = []
    base_amount = 0
    for line in body.procedures:
        proc = db.query(Procedure).filter(Procedure.id == line.procedure_id).first()
        if not proc:
            raise HTTPException(status_code=404, detail=f"Procedure id {line.procedure_id} not found.")
        if line.quantity < 1:
            raise HTTPException(status_code=400, detail="Quantity must be at least 1.")
        line_total = float(proc.base_price) * line.quantity
        base_amount += line_total
        line_items.append((proc, line, line_total))

    base_amount = round(base_amount, 2)

    payment = Payment(
        patient_id=patient.id,
        base_amount=base_amount,
        discount_amount=0,
        insurance_covered_amount=0,
        final_amount=base_amount,   # secretary may adjust this later
        status="pending",
        posted_by_doctor_id=current_user.id,
        notes=body.notes,
    )
    db.add(payment)
    db.flush()  # gets payment.id without committing yet

    completed_treatment_item_ids = []
    for proc, line, line_total in line_items:
        db.add(PatientProcedure(
            payment_id=payment.id,
            patient_id=patient.id,
            procedure_id=proc.id,
            quantity=line.quantity,
            price_charged=line_total,
            tooth_area=line.tooth_area,
        ))

        match_query = db.query(TreatmentItem).filter(
            TreatmentItem.patient_id == patient.id,
            TreatmentItem.procedure_id == proc.id,
            TreatmentItem.status == "pending",
        )
        match_query = (
            match_query.filter(TreatmentItem.tooth_area == line.tooth_area)
            if line.tooth_area else match_query.filter(TreatmentItem.tooth_area.is_(None))
        )
        matched_item = match_query.order_by(TreatmentItem.created_at.asc()).first()
        if matched_item:
            matched_item.status = "completed"
            matched_item.completed_at = func.now()
            completed_treatment_item_ids.append(matched_item.id)

    db.commit()
    db.refresh(payment)

    message = f"Charge posted for {patient.name}: {base_amount} EGP (pending)."
    if completed_treatment_item_ids:
        message += f" {len(completed_treatment_item_ids)} pending treatment item(s) marked completed."

    return {
        "payment_id": payment.id,
        "patient_name": patient.name,
        "base_amount": float(payment.base_amount),
        "status": payment.status,
        "treatment_items_completed": completed_treatment_item_ids,
        "message": message,
    }