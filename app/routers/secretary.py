from datetime import date, datetime, timedelta
from typing import Literal, Optional
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Appointment, Patient, Payment, PaymentAdjustment, Procedure, TreatmentItem, User
from ..auth import require_secretary, require_staff
from .. import receipts, reports, whatsapp
from ..config import CLINIC_NAME

router = APIRouter(prefix="/api/secretary", tags=["secretary"])

VALID_METHODS = {"cash", "card", "instapay", "vodafone_cash"}
CAIRO = ZoneInfo("Africa/Cairo")

NO_SHOW_MESSAGE = "معلش، لاحظنا انك متجتش لميعادك النهاردة. لو حابب تحجز ميعاد تاني قولنا 🦷"
CANCELLED_MESSAGE = f"تم إلغاء ميعادك في {CLINIC_NAME}. لو حابب تحجز ميعاد جديد قولنا 🦷"


class AdjustPaymentRequest(BaseModel):
    discount_amount: Optional[float] = None
    insurance_covered_amount: Optional[float] = None
    reason: Optional[str] = None


class UpdateAppointmentStatusRequest(BaseModel):
    status: Literal["done", "no_show", "cancelled"]


class UpdateWhatsappOptInRequest(BaseModel):
    opt_in: bool


class RescheduleAppointmentRequest(BaseModel):
    new_date: str  # YYYY-MM-DD
    new_time: str  # HH:MM (24-hour) -- no fixed-slot grid enforced here, unlike the
                   # WhatsApp agent's booking flow: a staff-initiated reschedule is a
                   # deliberate human decision, not an automated bot picking a time.


@router.get("/patients/search")
def search_patients(q: str, db: Session = Depends(get_db), current_user: User = Depends(require_secretary)):
    """Search patients by name or phone — mirrors doctor.py's identical
    endpoint (kept as a separate route since routes are gated per-role)."""
    results = db.query(Patient).filter(
        (Patient.name.ilike(f"%{q}%")) | (Patient.phone.ilike(f"%{q}%"))
    ).limit(10).all()
    return [{"id": p.id, "name": p.name, "phone": p.phone} for p in results]


@router.get("/patients/{patient_id}")
def patient_detail(patient_id: int, db: Session = Depends(get_db), current_user: User = Depends(require_secretary)):
    """Full per-patient view: profile, lifetime stats, appointment history,
    and payment/procedure history — the design doc's 'Per-Patient History'
    dashboard section, plus the whatsapp_opt_in flag so staff can see/manage
    it from the same screen the toggle endpoint updates."""
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

    total_spent = sum(float(p.final_amount) for p in payments if p.status == "paid")
    completed_visits = [a for a in appointments if a.status == "done"]
    last_visit = max((a.scheduled_at for a in completed_visits), default=None)
    no_show_count = sum(1 for a in appointments if a.status == "no_show")

    treatment_items = (
        db.query(TreatmentItem)
        .filter(TreatmentItem.patient_id == patient_id)
        .order_by(TreatmentItem.created_at.desc())
        .all()
    )

    return {
        "patient_id": patient.id,
        "name": patient.name,
        "phone": patient.phone,
        "age": patient.age,
        "gender": patient.gender,
        "whatsapp_opt_in": patient.whatsapp_opt_in,
        "created_at": patient.created_at.isoformat() if patient.created_at else None,
        "total_spent": round(total_spent, 2),
        "last_visit": last_visit.isoformat() if last_visit else None,
        "total_appointments": len(appointments),
        "no_show_count": no_show_count,
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


@router.get("/pending")
def list_pending_payments(db: Session = Depends(get_db), current_user: User = Depends(require_secretary)):
    """Everything the secretary still needs to collect, oldest first."""
    payments = (
        db.query(Payment)
        .filter(Payment.status.in_(["pending", "partial"]))
        .order_by(Payment.created_at.asc())
        .all()
    )
    results = []
    for p in payments:
        procedures = [
            {
                "procedure_name": pp.procedure.name,
                "quantity": pp.quantity,
                "price_charged": float(pp.price_charged),
                "tooth_area": pp.tooth_area,
            }
            for pp in p.procedures
        ]
        results.append({
            "payment_id": p.id,
            "patient_id": p.patient_id,
            "patient_name": p.patient.name,
            "patient_phone": p.patient.phone,
            "base_amount": float(p.base_amount),
            "discount_amount": float(p.discount_amount),
            "insurance_covered_amount": float(p.insurance_covered_amount),
            "final_amount": float(p.final_amount),
            "status": p.status,
            "notes": p.notes,
            "created_at": p.created_at.isoformat() if p.created_at else None,
            "procedures": procedures,
        })
    return results


@router.post("/payments/{payment_id}/adjust")
def adjust_payment(
    payment_id: int,
    body: AdjustPaymentRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_secretary),
):
    """Apply a discount and/or insurance coverage. Logs every change to
    payment_adjustments so there's always a record of who changed what and why
    (`reason` is where the secretary notes the insurance provider/terms)."""
    payment = db.query(Payment).filter(Payment.id == payment_id).first()
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found.")
    if payment.status == "paid":
        raise HTTPException(status_code=400, detail="Cannot adjust a payment that's already been collected.")

    if body.discount_amount is not None:
        if body.discount_amount < 0:
            raise HTTPException(status_code=400, detail="Discount cannot be negative.")
        db.add(PaymentAdjustment(
            payment_id=payment.id, adjusted_by=current_user.id,
            adjustment_type="discount",
            old_amount=payment.discount_amount, new_amount=body.discount_amount,
            reason=body.reason,
        ))
        payment.discount_amount = body.discount_amount

    if body.insurance_covered_amount is not None:
        if body.insurance_covered_amount < 0:
            raise HTTPException(status_code=400, detail="Insurance amount cannot be negative.")
        db.add(PaymentAdjustment(
            payment_id=payment.id, adjusted_by=current_user.id,
            adjustment_type="insurance",
            old_amount=payment.insurance_covered_amount, new_amount=body.insurance_covered_amount,
            reason=body.reason,
        ))
        payment.insurance_covered_amount = body.insurance_covered_amount

    new_final = float(payment.base_amount) - float(payment.discount_amount) - float(payment.insurance_covered_amount)
    if new_final < 0:
        raise HTTPException(status_code=400, detail="Discount + insurance cannot exceed the base amount.")
    payment.final_amount = round(new_final, 2)

    db.commit()
    db.refresh(payment)
    return {
        "payment_id": payment.id,
        "base_amount": float(payment.base_amount),
        "discount_amount": float(payment.discount_amount),
        "insurance_covered_amount": float(payment.insurance_covered_amount),
        "final_amount": float(payment.final_amount),
        "status": payment.status,
    }


@router.post("/payments/{payment_id}/collect")
def collect_payment(
    payment_id: int,
    method: str = Form("cash"),
    receipt: UploadFile | None = File(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_secretary),
):
    """Mark a payment as collected. `cash` needs no receipt; `card`/`instapay`/
    `vodafone_cash` require a receipt photo, enforced here. Multipart (not JSON)
    because it carries an optional file alongside the method.

    Best-effort sends the patient a WhatsApp confirmation + a PDF receipt
    (Payment.id doubles as the receipt number -- no separate Receipt model).
    Never fails the collect request if the WhatsApp send fails."""
    payment = db.query(Payment).filter(Payment.id == payment_id).first()
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found.")
    if payment.status == "paid":
        raise HTTPException(status_code=400, detail="This payment was already marked as paid.")
    if method not in VALID_METHODS:
        raise HTTPException(status_code=400, detail=f"Invalid method. Must be one of: {', '.join(sorted(VALID_METHODS))}.")
    if method != "cash" and (receipt is None or not receipt.filename):
        raise HTTPException(status_code=400, detail="A receipt photo is required for card, instapay, and vodafone cash payments.")

    receipt_path = receipts.save_receipt(payment.id, receipt) if (receipt is not None and receipt.filename) else None

    payment.status = "paid"
    payment.method = method
    payment.receipt_path = receipt_path
    payment.collected_by_secretary_id = current_user.id
    payment.paid_at = func.now()

    db.commit()
    db.refresh(payment)

    whatsapp_receipt_sent = False
    try:
        confirmation = f"تم استلام دفعتك بمبلغ {payment.final_amount:g} جنيه بنجاح. شكراً ليك 🦷"
        whatsapp.send_whatsapp_text(payment.patient, confirmation)
        pdf_bytes = reports.generate_payment_receipt_pdf(payment)
        whatsapp_receipt_sent = whatsapp.send_whatsapp_document(
            payment.patient, pdf_bytes, f"receipt_{payment.id}.pdf", caption="إيصال الدفع"
        )
    except Exception as e:
        print(f"WhatsApp receipt send failed for payment {payment.id}: {type(e).__name__}: {e}")

    return {
        "payment_id": payment.id,
        "final_amount": float(payment.final_amount),
        "method": payment.method,
        "receipt_path": payment.receipt_path,
        "status": payment.status,
        "whatsapp_receipt_sent": whatsapp_receipt_sent,
        "message": f"Collected {payment.final_amount} EGP via {payment.method}.",
    }


@router.get("/daily-total")
def daily_total(
    date_param: Optional[str] = Query(None, alias="date"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_secretary),
):
    """Totals for a given day (default today), computed in Cairo local time
    regardless of the DB server's timezone."""
    if date_param:
        try:
            target_date = datetime.strptime(date_param, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=400, detail="date must be in YYYY-MM-DD format.")
    else:
        target_date = date.today()

    day_cairo = func.date(func.timezone("Africa/Cairo", Payment.paid_at))

    row = (
        db.query(
            func.count(Payment.id),
            func.coalesce(func.sum(Payment.final_amount), 0),
            func.coalesce(func.sum(Payment.base_amount), 0),
            func.coalesce(func.sum(Payment.discount_amount), 0),
            func.coalesce(func.sum(Payment.insurance_covered_amount), 0),
        )
        .filter(Payment.status == "paid", day_cairo == target_date)
        .first()
    )
    count, net_collected, gross_billed, total_discounts, total_insurance = row
    return {
        "date": target_date.isoformat(),
        "day_of_week": target_date.strftime("%A"),
        "payments_collected": count,
        "net_collected": float(net_collected),
        "gross_billed": float(gross_billed),
        "total_discounts_given": float(total_discounts),
        "total_insurance_covered": float(total_insurance),
    }


@router.get("/appointments/today")
def todays_appointments(
    date_param: Optional[str] = Query(None, alias="date"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_secretary),
):
    """Every non-cancelled appointment scheduled for a given day (default today,
    Cairo-local), with a flag for whether that patient already has an unpaid
    charge that day so the secretary can jump straight into the collect flow.
    Same ?date= convention as /transactions and /daily-total, so the frontend's
    one day picker drives all three cards together."""
    if date_param:
        try:
            target_date = datetime.strptime(date_param, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=400, detail="date must be in YYYY-MM-DD format.")
    else:
        target_date = date.today()

    day_cairo = func.date(func.timezone("Africa/Cairo", Appointment.scheduled_at))

    appts = (
        db.query(Appointment)
        .filter(day_cairo == target_date, Appointment.status != "cancelled")
        .order_by(Appointment.scheduled_at.asc())
        .all()
    )

    pending_day = func.date(func.timezone("Africa/Cairo", Payment.created_at))
    patient_ids_with_pending = {
        pid for (pid,) in db.query(Payment.patient_id)
        .filter(Payment.status.in_(["pending", "partial"]), pending_day == target_date)
        .all()
    }

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
        "doctor_name": a.doctor.name if a.doctor else None,
        "scheduled_at": a.scheduled_at.isoformat(),
        "status": a.status,
        "appointment_type": a.appointment_type,
        "payment_status": payment_by_appt.get(a.id),
        "has_pending_payment_today": a.patient_id in patient_ids_with_pending,
    } for a in appts]


@router.patch("/appointments/{appointment_id}/status")
def update_appointment_status(
    appointment_id: int,
    body: UpdateAppointmentStatusRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_staff),
):
    """Mark an appointment as done (patient was seen), no_show (patient never
    showed up), or cancelled. Manual, staff-triggered only -- there's no
    automatic detection of a missed appointment, since that would risk
    flagging a visit the secretary just hasn't gotten around to marking done
    yet. On no_show/cancelled, best-effort sends a WhatsApp notice -- a
    failed send never fails this request, matching
    app/whatsapp.py's send_whatsapp_text contract.

    Open to both roles (require_staff, not require_secretary): the doctor
    needs to mark a visit done themselves right after charging/planning
    treatment for it, without waiting on the secretary.

    Marking a 'treatment'-typed appointment done also closes out its linked
    TreatmentItem and auto-creates the payment due for it (priced from the
    item's own procedure, same pattern as doctor.py's /charges), with a
    best-effort WhatsApp notice of the amount due."""
    appt = db.query(Appointment).filter(Appointment.id == appointment_id).first()
    if not appt:
        raise HTTPException(status_code=404, detail="Appointment not found.")
    if appt.status in ("done", "no_show", "cancelled"):
        raise HTTPException(status_code=400, detail=f"This appointment is already marked {appt.status}.")

    appt.status = body.status
    db.commit()
    db.refresh(appt)

    whatsapp_sent = None
    payment_created_id = None
    if body.status == "no_show":
        whatsapp_sent = whatsapp.send_whatsapp_text(appt.patient, NO_SHOW_MESSAGE)
    elif body.status == "cancelled":
        whatsapp_sent = whatsapp.send_whatsapp_text(appt.patient, CANCELLED_MESSAGE)
    elif body.status == "done" and appt.appointment_type == "treatment" and appt.treatment_item_id:
        item = db.query(TreatmentItem).filter(TreatmentItem.id == appt.treatment_item_id).first()
        if item and item.status != "completed":
            item.status = "completed"
            item.completed_at = func.now()

            proc = db.query(Procedure).filter(Procedure.id == item.procedure_id).first()
            amount = float(proc.base_price) if proc else 0.0
            payment = Payment(
                patient_id=appt.patient_id,
                appointment_id=appt.id,
                treatment_item_id=item.id,
                base_amount=amount,
                final_amount=amount,
                status="pending",
            )
            db.add(payment)
            db.commit()
            db.refresh(payment)
            payment_created_id = payment.id

            proc_name = proc.name if proc else "العلاج"
            message = (
                f"تم الانتهاء من {proc_name}. المبلغ المطلوب {amount:g} جنيه — "
                f"ممكن تدفعه في العيادة. شكراً ليك 🦷"
            )
            whatsapp_sent = whatsapp.send_whatsapp_text(appt.patient, message)

    return {
        "appointment_id": appt.id,
        "status": appt.status,
        "whatsapp_sent": whatsapp_sent,
        "payment_created_id": payment_created_id,
        "message": f"Appointment {appt.id} marked as {appt.status}.",
    }


@router.patch("/appointments/{appointment_id}/reschedule")
def reschedule_appointment(
    appointment_id: int,
    body: RescheduleAppointmentRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_secretary),
):
    """Move a confirmed appointment to a new date/time. Guarded by the same
    double-booking safety net the WhatsApp agent's own reschedule tool
    relies on: a pre-check query, then an IntegrityError catch on the
    partial unique index as the race-proof backstop. Best-effort WhatsApp
    notice to the patient afterward."""
    appt = db.query(Appointment).filter(Appointment.id == appointment_id).first()
    if not appt:
        raise HTTPException(status_code=404, detail="Appointment not found.")
    if appt.status != "confirmed":
        raise HTTPException(
            status_code=400,
            detail=f"Cannot reschedule an appointment that's {appt.status} -- only confirmed appointments can be rescheduled.",
        )

    try:
        new_date = datetime.strptime(body.new_date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="new_date must be in YYYY-MM-DD format.")
    try:
        new_time = datetime.strptime(body.new_time, "%H:%M").time()
    except ValueError:
        raise HTTPException(status_code=400, detail="new_time must be in HH:MM (24-hour) format.")

    new_scheduled_at = datetime.combine(new_date, new_time, tzinfo=CAIRO)
    if new_scheduled_at < datetime.now(CAIRO):
        raise HTTPException(status_code=400, detail="Cannot reschedule to a time in the past.")

    conflict = (
        db.query(Appointment)
        .filter(
            Appointment.doctor_id == appt.doctor_id,
            Appointment.scheduled_at == new_scheduled_at,
            Appointment.status != "cancelled",
            Appointment.id != appt.id,
        )
        .first()
    )
    if conflict:
        raise HTTPException(status_code=400, detail="That slot is already taken. Please choose another time.")

    appt.scheduled_at = new_scheduled_at
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="That slot was just taken. Please choose another time.")
    db.refresh(appt)

    local_time_str = appt.scheduled_at.astimezone(CAIRO).strftime("%Y-%m-%d %H:%M")
    message = f"تم تغيير ميعادك ليكون يوم {local_time_str} في {CLINIC_NAME}. لو محتاج تعديل تاني قولنا 🦷"
    whatsapp_sent = whatsapp.send_whatsapp_text(appt.patient, message)

    return {
        "appointment_id": appt.id,
        "scheduled_at": appt.scheduled_at.isoformat(),
        "whatsapp_sent": whatsapp_sent,
        "message": f"Appointment {appt.id} rescheduled to {local_time_str}.",
    }


@router.patch("/patients/{patient_id}/whatsapp-opt-in")
def update_whatsapp_opt_in(
    patient_id: int,
    body: UpdateWhatsappOptInRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_secretary),
):
    """Staff-facing toggle for when a patient asks to stop receiving proactive
    WhatsApp messages (reminders, re-engagement, no-show follow-ups). Does NOT
    affect the conversational booking agent -- that only ever replies to a
    message the patient sent, which isn't what opt-in/out governs."""
    patient = db.query(Patient).filter(Patient.id == patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found.")

    patient.whatsapp_opt_in = body.opt_in
    db.commit()
    db.refresh(patient)
    return {
        "patient_id": patient.id,
        "whatsapp_opt_in": patient.whatsapp_opt_in,
        "message": f"{patient.name} {'opted in to' if body.opt_in else 'opted out of'} WhatsApp messages.",
    }


@router.get("/transactions")
def transactions_for_day(
    date_param: Optional[str] = Query(None, alias="date"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_secretary),
):
    """Itemized list of a given day's (default today) collected payments, one
    row per transaction, plus the grand total — a sibling to /daily-total,
    which stays aggregate-only. Same ?date= convention as /daily-total, so the
    two can be driven by the same date picker on the frontend."""
    if date_param:
        try:
            target_date = datetime.strptime(date_param, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=400, detail="date must be in YYYY-MM-DD format.")
    else:
        target_date = date.today()

    day_cairo = func.date(func.timezone("Africa/Cairo", Payment.paid_at))

    payments = (
        db.query(Payment)
        .filter(Payment.status == "paid", day_cairo == target_date)
        .order_by(Payment.paid_at.asc())
        .all()
    )
    rows = [{
        "payment_id": p.id,
        "patient_name": p.patient.name,
        "method": p.method,
        "final_amount": float(p.final_amount),
        "paid_at": p.paid_at.isoformat() if p.paid_at else None,
    } for p in payments]
    total = round(sum(r["final_amount"] for r in rows), 2)
    return {"transactions": rows, "total": total}


@router.get("/revenue-trend")
def revenue_trend(
    days: int = Query(14, ge=1, le=90),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_secretary),
):
    """Daily collected revenue for the last `days` days (Cairo-local, today
    inclusive), zero-filled so a trend chart gets one point per day even on
    days with no collections — a plain GROUP BY would silently skip those."""
    end_date = date.today()
    start_date = end_date - timedelta(days=days - 1)
    day_cairo = func.date(func.timezone("Africa/Cairo", Payment.paid_at))

    rows = (
        db.query(day_cairo.label("day"), func.coalesce(func.sum(Payment.final_amount), 0).label("revenue"))
        .filter(Payment.status == "paid", day_cairo >= start_date, day_cairo <= end_date)
        .group_by(day_cairo)
        .all()
    )
    by_day = {r.day.isoformat(): float(r.revenue) for r in rows}
    return [
        {
            "date": (start_date + timedelta(days=n)).isoformat(),
            "revenue": by_day.get((start_date + timedelta(days=n)).isoformat(), 0.0),
        }
        for n in range(days)
    ]
