import io

import arabic_reshaper
from bidi.algorithm import get_display
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from .config import CLINIC_NAME

# Tahoma has robust Arabic glyph coverage and ships with every Windows install --
# fine since report generation currently only ever runs on the clinic's own
# Windows machine. Before deploying to non-Windows hosting, swap this for a
# bundled TTF (e.g. Noto Naskh Arabic) shipped in the repo, so PDF generation
# stops depending on the host OS having this exact file.
_FONT_PATH = r"C:\Windows\Fonts\tahoma.ttf"
_FONT_NAME = "Tahoma"
pdfmetrics.registerFont(TTFont(_FONT_NAME, _FONT_PATH))


def _shape(text: str) -> str:
    """Reshape + bidi-reorder text containing Arabic into the visual form
    reportlab needs -- without this, Arabic renders as disconnected,
    logically-ordered (i.e. backwards-looking) letters instead of connected
    RTL script. Safe to call on pure-ASCII text too (no-op)."""
    if any("؀" <= ch <= "ۿ" for ch in text):
        return get_display(arabic_reshaper.reshape(text))
    return text


def generate_monthly_report_pdf(data: dict) -> bytes:
    """Renders the exact dict analytics.get_or_build_monthly_analytics
    returns into a PDF -- the design doc's 'PDF monthly report for doctor'.
    Paginates defensively if content runs past one page; a normal month's
    data (a handful of procedures, a few insight bullets) fits on one."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    width, height = A4
    right_margin = width - 20 * mm
    top_start = height - 20 * mm
    bottom_margin = 20 * mm
    y = top_start

    def line(text, size=11, color=colors.black, gap=7 * mm):
        nonlocal y
        if y < bottom_margin:
            c.showPage()
            y = top_start
        c.setFont(_FONT_NAME, size)
        c.setFillColor(color)
        c.drawRightString(right_margin, y, _shape(text))
        y -= gap

    line(CLINIC_NAME, size=18, gap=10 * mm)
    line(f"تقرير شهري -- {data['month']}", size=14, gap=10 * mm)

    line(f"الإيرادات: {data['total_revenue']} جنيه من {data['paid_count']} عملية دفع", size=12)
    if data.get("revenue_change_pct") is not None:
        direction = "زيادة" if data["revenue_change_pct"] >= 0 else "نقص"
        line(f"مقارنة بالشهر السابق ({data['previous_month']}): {direction} {abs(data['revenue_change_pct'])}%", size=11)
    line(
        f"عدد المرضى: {data['distinct_patients_charged']} "
        f"(جدد: {data['new_patients']}، عائدين: {data['returning_patients']})",
        size=11,
    )
    line(f"مرضى غير نشطين (6+ شهور بدون زيارة): {data['inactive_patients_count']}", size=11)
    line(
        f"حالات الغياب عن الميعاد: {data['no_show_count']} "
        f"(خسارة إيرادات تقديرية {data['no_show_estimated_lost_revenue']} جنيه)",
        size=11,
    )
    line(f"الخصومات: {data['total_discounts_given']} جنيه  |  التأمين: {data['total_insurance_covered']} جنيه", size=11)

    y -= 3 * mm
    line("أكثر الإجراءات:", size=12, gap=8 * mm, color=colors.HexColor("#2F6F62"))
    if not data["top_procedures"]:
        line("لا توجد إجراءات مسجلة هذا الشهر.", size=10)
    for p in data["top_procedures"]:
        line(f"{p['procedure']} × {p['count']} = {p['revenue']} جنيه", size=10, gap=6 * mm)

    y -= 3 * mm
    line("ملاحظات:", size=12, gap=8 * mm, color=colors.HexColor("#2F6F62"))
    for para in data["insights"].split("\n"):
        para = para.strip()
        if para:
            line(para, size=10, gap=6 * mm)

    c.showPage()
    c.save()
    return buf.getvalue()


def generate_payment_receipt_pdf(payment) -> bytes:
    """Renders a one-page receipt for a single (paid) Payment row -- the
    payment's own id doubles as the receipt/invoice number, so there's no
    separate Receipt model. Treatment/procedure lines come from
    Payment.procedures (PatientProcedure) when present (a doctor-posted
    charge or a completed treatment-plan item); a bare consultation-fee
    payment has none, so that section is simply skipped."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    width, height = A4
    right_margin = width - 20 * mm
    top_start = height - 20 * mm
    bottom_margin = 20 * mm
    y = top_start

    def line(text, size=11, color=colors.black, gap=7 * mm):
        nonlocal y
        if y < bottom_margin:
            c.showPage()
            y = top_start
        c.setFont(_FONT_NAME, size)
        c.setFillColor(color)
        c.drawRightString(right_margin, y, _shape(text))
        y -= gap

    paid_at = payment.paid_at.strftime("%Y-%m-%d %H:%M") if payment.paid_at else "-"

    line(CLINIC_NAME, size=18, gap=10 * mm)
    line(f"إيصال دفع رقم {payment.id}", size=14, gap=10 * mm)

    line(f"المريض: {payment.patient.name}", size=12)
    line(f"التاريخ: {paid_at}", size=12)
    line(f"طريقة الدفع: {payment.method}", size=12, gap=9 * mm)

    if payment.procedures:
        line("الإجراءات:", size=12, gap=8 * mm, color=colors.HexColor("#2F6F62"))
        for pp in payment.procedures:
            tooth = f" ({pp.tooth_area})" if pp.tooth_area else ""
            line(f"{pp.procedure.name}{tooth} × {pp.quantity} = {pp.price_charged} جنيه", size=10, gap=6 * mm)
        y -= 3 * mm

    if payment.discount_amount:
        line(f"الخصم: {payment.discount_amount} جنيه", size=11)
    if payment.insurance_covered_amount:
        line(f"التأمين: {payment.insurance_covered_amount} جنيه", size=11)

    line(f"الإجمالي المدفوع: {payment.final_amount} جنيه", size=13, gap=9 * mm, color=colors.HexColor("#2F6F62"))
    line("تم الدفع بنجاح -- شكراً لك", size=11)

    c.showPage()
    c.save()
    return buf.getvalue()
