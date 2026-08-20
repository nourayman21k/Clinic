import calendar
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .models import Payment, PatientProcedure, Procedure, Patient, Appointment, AnalyticsSnapshot
from . import voice_charge  # reuses voice_charge._llm (ChatGroq singleton) — avoids a second Groq client

NO_DATA_MESSAGE = "لا توجد بيانات كافية عن هذا الشهر لإنشاء ملاحظات."


def get_month_range(year: int, month: int):
    start = date(year, month, 1)
    end = date(year, month, calendar.monthrange(year, month)[1])
    return start, end


def generate_revenue_summary(db: Session, year: int, month: int) -> dict:
    start, end = get_month_range(year, month)
    paid_date = func.date(func.timezone("Africa/Cairo", Payment.paid_at))
    total, count = (
        db.query(func.coalesce(func.sum(Payment.final_amount), 0), func.count(Payment.id))
        .filter(Payment.status == "paid", paid_date.between(start, end))
        .first()
    )
    return {"month": f"{year}-{month:02d}", "total_revenue": round(float(total), 2), "paid_count": count}


def get_procedure_revenue_breakdown(db: Session, year: int, month: int, limit: Optional[int] = None) -> list:
    start, end = get_month_range(year, month)
    performed_date = func.date(func.timezone("Africa/Cairo", PatientProcedure.performed_at))
    q = (
        db.query(
            Procedure.name,
            func.count(PatientProcedure.id),
            func.coalesce(func.sum(PatientProcedure.price_charged), 0),
        )
        .join(Procedure, PatientProcedure.procedure_id == Procedure.id)
        .filter(performed_date.between(start, end))
        .group_by(Procedure.id, Procedure.name)
        .order_by(func.sum(PatientProcedure.price_charged).desc())
    )
    if limit:
        q = q.limit(limit)
    return [{"procedure": name, "count": count, "revenue": float(revenue)} for name, count, revenue in q.all()]


def get_discount_insurance_totals(db: Session, year: int, month: int) -> dict:
    start, end = get_month_range(year, month)
    paid_date = func.date(func.timezone("Africa/Cairo", Payment.paid_at))
    discount, insurance = (
        db.query(
            func.coalesce(func.sum(Payment.discount_amount), 0),
            func.coalesce(func.sum(Payment.insurance_covered_amount), 0),
        )
        .filter(Payment.status == "paid", paid_date.between(start, end))
        .first()
    )
    return {"total_discounts_given": float(discount), "total_insurance_covered": float(insurance)}


def get_distinct_patients_charged(db: Session, year: int, month: int) -> int:
    start, end = get_month_range(year, month)
    performed_date = func.date(func.timezone("Africa/Cairo", PatientProcedure.performed_at))
    return db.query(func.count(func.distinct(PatientProcedure.patient_id))).filter(
        performed_date.between(start, end)
    ).scalar() or 0


def flag_inactive_patients(db: Session, months_threshold: int = 6) -> list:
    """Patients whose most recent COMPLETED visit (status='done') is older
    than months_threshold, or who have never completed one. A future booking
    or a no-show doesn't count as activity — ported from the notebook's
    flag_inactive_patients, same semantics."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=30 * months_threshold)
    last_visit = func.max(Appointment.scheduled_at)
    rows = (
        db.query(Patient.id, Patient.name, Patient.phone, last_visit.label("last_visit"))
        .outerjoin(Appointment, (Appointment.patient_id == Patient.id) & (Appointment.status == "done"))
        .group_by(Patient.id, Patient.name, Patient.phone)
        .having(or_(last_visit.is_(None), last_visit < cutoff))
        .all()
    )
    return [{"patient_id": r.id, "name": r.name, "phone": r.phone,
             "last_visit": r.last_visit.isoformat() if r.last_visit else None} for r in rows]


def get_new_vs_returning_ratio(db: Session, year: int, month: int) -> dict:
    """Of the patients with an appointment THIS month, how many had a prior
    (any earlier Cairo-date) appointment vs. none. Ported from the notebook's
    correlated-subquery version."""
    start, end = get_month_range(year, month)
    appt_date = func.date(func.timezone("Africa/Cairo", Appointment.scheduled_at))
    prior_count = (
        db.query(func.count(Appointment.id))
        .filter(Appointment.patient_id == Patient.id, appt_date < start)
        .correlate(Patient)
        .scalar_subquery()
    )
    rows = (
        db.query(Patient.id, prior_count.label("prior_count"))
        .join(Appointment, Appointment.patient_id == Patient.id)
        .filter(appt_date.between(start, end))
        .group_by(Patient.id)
        .all()
    )
    new_count = sum(1 for r in rows if r.prior_count == 0)
    returning_count = sum(1 for r in rows if r.prior_count > 0)
    return {"new_patients": new_count, "returning_patients": returning_count}


def calculate_no_show_impact(db: Session, year: int, month: int) -> dict:
    """No-show count this month x the ALL-TIME average paid amount (not
    month-scoped) -- same asymmetry as the notebook version, intentional:
    it estimates lost revenue using a stable average, not a possibly-thin
    single-month one."""
    start, end = get_month_range(year, month)
    appt_date = func.date(func.timezone("Africa/Cairo", Appointment.scheduled_at))
    no_show_count = (
        db.query(func.count(Appointment.id))
        .filter(Appointment.status == "no_show", appt_date.between(start, end))
        .scalar() or 0
    )
    avg_payment = db.query(func.avg(Payment.final_amount)).filter(Payment.status == "paid").scalar()
    avg_payment = float(avg_payment or 0)
    return {"no_show_count": no_show_count, "estimated_lost_revenue": round(no_show_count * avg_payment, 2)}


def get_month_over_month_change(db: Session, year: int, month: int) -> dict:
    """This month's revenue vs. the prior calendar month's, as an absolute
    and percentage change. 0 previous revenue -> pct change is null rather
    than a division-by-zero/infinite spike."""
    prev_year, prev_month = (year - 1, 12) if month == 1 else (year, month - 1)
    current = generate_revenue_summary(db, year, month)["total_revenue"]
    previous = generate_revenue_summary(db, prev_year, prev_month)["total_revenue"]
    change = round(current - previous, 2)
    pct_change = round((change / previous) * 100, 1) if previous else None
    return {"previous_month": f"{prev_year}-{prev_month:02d}", "previous_revenue": previous,
            "revenue_change": change, "revenue_change_pct": pct_change}


def generate_monthly_insights(revenue: dict, breakdown: list, totals: dict, patients: int,
                               inactive_count: int, ratio: dict, no_shows: dict, mom: dict) -> str:
    """Aggregates the numbers and asks the LLM for short Arabic business
    insights — skips the LLM call entirely when there's nothing to report."""
    if revenue["paid_count"] == 0 and not breakdown:
        return NO_DATA_MESSAGE

    mom_line = (
        f"مقارنة بالشهر اللي فات ({mom['previous_month']}): "
        f"{'زيادة' if mom['revenue_change'] >= 0 else 'نقص'} قدره {abs(mom['revenue_change'])} جنيه"
        + (f" ({mom['revenue_change_pct']}%)." if mom['revenue_change_pct'] is not None else ".")
    )

    data_summary = f"""
الإيرادات هذا الشهر: {revenue['total_revenue']} جنيه من {revenue['paid_count']} عملية دفع.
{mom_line}
عدد المرضى المميزين اللي اتعالجوا الشهر ده: {patients} ({ratio['new_patients']} جديد، {ratio['returning_patients']} عائد).
توزيع الإيرادات حسب الإجراء: {breakdown}
إجمالي الخصومات: {totals['total_discounts_given']} جنيه.
إجمالي التغطية التأمينية: {totals['total_insurance_covered']} جنيه.
عدد مرضى غير نشطين (6 شهور فأكتر من غير زيارة): {inactive_count}.
عدد حالات الغياب عن الميعاد الشهر ده: {no_shows['no_show_count']} (خسارة إيرادات تقديرية {no_shows['estimated_lost_revenue']} جنيه).
"""

    prompt = f"""أنت محلل بيانات لعيادة أسنان مصرية. بناءً على البيانات التالية، اكتب 3 إلى 5 ملاحظات عملية ومفيدة للدكتور، باللغة العربية المصرية، بأسلوب مباشر وواضح.

البيانات:
{data_summary}

اكتب الملاحظات كنقاط قصيرة، كل نقطة تركز على حاجة واحدة يقدر الدكتور ياخد قرار بناء عليها."""

    response = voice_charge._llm.invoke(prompt)
    return response.content


def build_monthly_analytics(db: Session, year: int, month: int) -> dict:
    revenue = generate_revenue_summary(db, year, month)
    breakdown = get_procedure_revenue_breakdown(db, year, month)
    totals = get_discount_insurance_totals(db, year, month)
    patients = get_distinct_patients_charged(db, year, month)
    inactive = flag_inactive_patients(db)
    ratio = get_new_vs_returning_ratio(db, year, month)
    no_shows = calculate_no_show_impact(db, year, month)
    mom = get_month_over_month_change(db, year, month)
    insights = generate_monthly_insights(revenue, breakdown, totals, patients, len(inactive), ratio, no_shows, mom)
    return {
        "month": revenue["month"],
        "total_revenue": revenue["total_revenue"],
        "paid_count": revenue["paid_count"],
        "distinct_patients_charged": patients,
        "total_discounts_given": totals["total_discounts_given"],
        "total_insurance_covered": totals["total_insurance_covered"],
        "top_procedures": breakdown[:5],
        "procedure_breakdown": breakdown,
        "inactive_patients_count": len(inactive),
        "new_patients": ratio["new_patients"],
        "returning_patients": ratio["returning_patients"],
        "no_show_count": no_shows["no_show_count"],
        "no_show_estimated_lost_revenue": no_shows["estimated_lost_revenue"],
        "previous_month": mom["previous_month"],
        "previous_month_revenue": mom["previous_revenue"],
        "revenue_change": mom["revenue_change"],
        "revenue_change_pct": mom["revenue_change_pct"],
        "insights": insights,
    }


def get_or_build_monthly_analytics(db: Session, year: int, month: int) -> dict:
    """The current (still-changing) month is always computed fresh. A past,
    CLOSED month is served from analytics_snapshots if already cached there;
    otherwise it's computed once and cached for next time. This is what lets
    the scheduler's monthly job pre-warm last month's snapshot while still
    keeping on-demand recompute available for a month that was never
    scheduler-triggered (e.g. the backend was down on the 1st)."""
    today = date.today()
    is_current_month = (year, month) == (today.year, today.month)
    if not is_current_month:
        cached = (
            db.query(AnalyticsSnapshot)
            .filter(AnalyticsSnapshot.year == year, AnalyticsSnapshot.month == month)
            .first()
        )
        if cached:
            return cached.snapshot

    result = build_monthly_analytics(db, year, month)

    if not is_current_month:
        db.add(AnalyticsSnapshot(year=year, month=month, snapshot=result, insights=result["insights"]))
        try:
            db.commit()
        except IntegrityError:
            # Two concurrent requests raced to cache the same month -- benign,
            # whichever committed first wins, this request just uses its own
            # freshly-computed (identical) result rather than re-querying.
            db.rollback()

    return result
