"""One-off: wipe all clinic data for a from-scratch demo recording, while
keeping the schema and login accounts (users, doctors) intact so staff can
log in and start using the app immediately.

TRUNCATE ... RESTART IDENTITY CASCADE also resets auto-increment ids (so the
demo starts at id=1 again) and follows FK references automatically, so table
order here doesn't matter.

NOT run as part of app startup or any migration chain -- this is destructive
and was run once, manually, at the user's explicit request."""
from sqlalchemy import text
from app.database import engine

TABLES_TO_WIPE = [
    "patients",
    "appointments",
    "procedures",
    "payments",
    "patient_procedures",
    "treatment_items",
    "payment_adjustments",
    "analytics_snapshots",
    "processed_whatsapp_messages",
    "failed_whatsapp_sends",
    "call_logs",  # not referenced by current code (legacy), wiped for consistency
]

with engine.begin() as conn:
    conn.execute(text(f"TRUNCATE TABLE {', '.join(TABLES_TO_WIPE)} RESTART IDENTITY CASCADE"))
    print(f"Wiped {len(TABLES_TO_WIPE)} tables: {', '.join(TABLES_TO_WIPE)}")

print("Kept intact: users, doctors (so you can log in and demo immediately).")
