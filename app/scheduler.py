from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import func

from . import analytics, email_sender, reports, whatsapp
from .config import CLINIC_NAME
from .database import SessionLocal
from .models import Appointment, Patient

CAIRO = ZoneInfo("Africa/Cairo")

_scheduler: BackgroundScheduler | None = None


def _reminder_text(appt: Appointment) -> str:
    local_time = appt.scheduled_at.astimezone(CAIRO).strftime("%Y-%m-%d %H:%M")
    return (
        f"تذكير: عندك ميعاد بكرة الساعة {local_time} في {CLINIC_NAME}. "
        f"لو محتاج تغيير الميعاد قولنا 🦷"
    )


def send_appointment_reminders() -> None:
    """Daily job: WhatsApp reminder for tomorrow's confirmed appointments that
    haven't been reminded yet. Each appointment commits independently -- one
    bad send must never block the rest of the batch."""
    db = SessionLocal()
    try:
        tomorrow = date.today() + timedelta(days=1)
        day_cairo = func.date(func.timezone("Africa/Cairo", Appointment.scheduled_at))
        appts = (
            db.query(Appointment)
            .filter(Appointment.status == "confirmed", Appointment.reminder_sent == False,  # noqa: E712
                     day_cairo == tomorrow)
            .all()
        )
        print(f"[scheduler] send_appointment_reminders: {len(appts)} appointment(s) for {tomorrow}")
        for appt in appts:
            sent = whatsapp.send_whatsapp_text(appt.patient, _reminder_text(appt))
            if sent:
                appt.reminder_sent = True
                db.commit()
            else:
                db.rollback()
    except Exception as e:
        print(f"[scheduler] send_appointment_reminders failed: {type(e).__name__}: {e}")
    finally:
        db.close()


def run_monthly_analytics_snapshot() -> None:
    """Monthly job: pre-compute + cache LAST month's analytics snapshot (the
    month that just closed), so the dashboard doesn't recompute it on first
    view -- then render it as a PDF and email it to the doctor (the design
    doc's 'PDF monthly report'). The email step is best-effort: if SMTP
    isn't configured, email_sender logs and skips without raising, so a
    missing mail setup never breaks the snapshot caching itself."""
    db = SessionLocal()
    try:
        today = date.today()
        prev_year, prev_month = (today.year - 1, 12) if today.month == 1 else (today.year, today.month - 1)
        result = analytics.get_or_build_monthly_analytics(db, prev_year, prev_month)
        print(f"[scheduler] run_monthly_analytics_snapshot: cached {prev_year}-{prev_month:02d}, "
              f"revenue={result['total_revenue']}")

        pdf_bytes = reports.generate_monthly_report_pdf(result)
        sent = email_sender.send_monthly_report_email(pdf_bytes, prev_year, prev_month)
        print(f"[scheduler] monthly report email: {'sent' if sent else 'skipped/failed'}")
    except Exception as e:
        print(f"[scheduler] run_monthly_analytics_snapshot failed: {type(e).__name__}: {e}")
    finally:
        db.close()


def send_reengagement_messages() -> None:
    """Monthly job: nudge patients flagged inactive (6+ months since their
    last completed visit). Stamps last_reengagement_sent_at for observability
    -- not currently used to suppress repeats, since the monthly cadence is
    already the natural rate limit."""
    db = SessionLocal()
    try:
        inactive = analytics.flag_inactive_patients(db)
        print(f"[scheduler] send_reengagement_messages: {len(inactive)} inactive patient(s)")
        for row in inactive:
            patient = db.query(Patient).filter(Patient.id == row["patient_id"]).first()
            if not patient:
                continue
            text = f"مشتاقين لك في {CLINIC_NAME}! لو حابب تحجز ميعاد كشف أو متابعة قولنا 🦷"
            sent = whatsapp.send_whatsapp_text(patient, text)
            if sent:
                patient.last_reengagement_sent_at = datetime.now(timezone.utc)
                db.commit()
            else:
                db.rollback()
    except Exception as e:
        print(f"[scheduler] send_reengagement_messages failed: {type(e).__name__}: {e}")
    finally:
        db.close()


def start_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        return
    _scheduler = BackgroundScheduler(timezone=CAIRO)
    _scheduler.add_job(send_appointment_reminders, CronTrigger(hour=9, minute=0), id="daily_reminders")
    _scheduler.add_job(run_monthly_analytics_snapshot, CronTrigger(day=1, hour=8, minute=0), id="monthly_analytics")
    _scheduler.add_job(send_reengagement_messages, CronTrigger(day=1, hour=8, minute=15), id="monthly_reengagement")
    _scheduler.start()
    print("[scheduler] started: daily reminders (09:00), monthly analytics + re-engagement (1st, 08:00/08:15) Cairo time")


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
