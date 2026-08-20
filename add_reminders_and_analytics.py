from sqlalchemy import text
from app.database import engine

with engine.begin() as conn:
    conn.execute(text(
        "ALTER TABLE appointments ADD COLUMN IF NOT EXISTS reminder_sent BOOLEAN DEFAULT FALSE"
    ))
    print("Migration complete: appointments.reminder_sent is ready.")

    conn.execute(text(
        "ALTER TABLE patients ADD COLUMN IF NOT EXISTS last_reengagement_sent_at TIMESTAMPTZ"
    ))
    print("Migration complete: patients.last_reengagement_sent_at is ready.")

    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS analytics_snapshots (
            id SERIAL PRIMARY KEY,
            year INTEGER NOT NULL,
            month INTEGER NOT NULL,
            snapshot JSONB NOT NULL,
            insights TEXT,
            generated_at TIMESTAMPTZ DEFAULT NOW(),
            CONSTRAINT uq_analytics_snapshot_month UNIQUE (year, month)
        )
    """))
    print("Migration complete: analytics_snapshots table is ready.")
