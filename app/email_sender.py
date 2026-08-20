import os
import smtplib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from dotenv import load_dotenv

load_dotenv()

SMTP_HOST = os.getenv("SMTP_HOST")
SMTP_PORT = os.getenv("SMTP_PORT")
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
DOCTOR_EMAIL = os.getenv("DOCTOR_EMAIL")


def send_monthly_report_email(pdf_bytes: bytes, year: int, month: int) -> bool:
    """Best-effort email of the monthly PDF report to the doctor. Never
    raises -- matches app/whatsapp.py's send contract: a scheduler job must
    not fail just because email isn't configured or delivery fails."""
    if not (SMTP_HOST and SMTP_PORT and SMTP_USER and SMTP_PASSWORD and DOCTOR_EMAIL):
        print(
            "Monthly report email skipped: SMTP not configured -- set "
            "SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, DOCTOR_EMAIL in .env."
        )
        return False

    try:
        msg = MIMEMultipart()
        msg["Subject"] = f"Clinic monthly report -- {year}-{month:02d}"
        msg["From"] = SMTP_USER
        msg["To"] = DOCTOR_EMAIL
        msg.attach(MIMEText(f"Attached: the clinic's analytics report for {year}-{month:02d}.", "plain"))

        attachment = MIMEApplication(pdf_bytes, _subtype="pdf")
        attachment.add_header("Content-Disposition", "attachment", filename=f"report_{year}_{month:02d}.pdf")
        msg.attach(attachment)

        with smtplib.SMTP(SMTP_HOST, int(SMTP_PORT), timeout=15) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)
        return True
    except Exception as e:
        print(f"Monthly report email failed: {type(e).__name__}: {e}")
        return False
