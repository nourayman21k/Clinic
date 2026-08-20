from sqlalchemy import text
from app.database import engine

with engine.begin() as conn:
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS treatment_items (
            id SERIAL PRIMARY KEY,
            patient_id INTEGER NOT NULL REFERENCES patients(id),
            procedure_id INTEGER NOT NULL REFERENCES procedures(id),
            tooth_area TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            notes TEXT,
            created_at TIMESTAMPTZ DEFAULT now(),
            completed_at TIMESTAMPTZ
        )
    """))
    print("Migration complete: treatment_items table is ready.")

    conn.execute(text(
        "ALTER TABLE appointments ADD COLUMN IF NOT EXISTS appointment_type TEXT NOT NULL DEFAULT 'consultation'"
    ))
    conn.execute(text(
        "ALTER TABLE appointments ADD COLUMN IF NOT EXISTS treatment_item_id INTEGER REFERENCES treatment_items(id)"
    ))
    print("Migration complete: appointments.appointment_type / treatment_item_id are ready.")

    conn.execute(text(
        "ALTER TABLE payments ADD COLUMN IF NOT EXISTS appointment_id INTEGER REFERENCES appointments(id)"
    ))
    conn.execute(text(
        "ALTER TABLE payments ADD COLUMN IF NOT EXISTS treatment_item_id INTEGER REFERENCES treatment_items(id)"
    ))
    print("Migration complete: payments.appointment_id / treatment_item_id are ready.")

    conn.execute(text(
        "INSERT INTO procedures (name, base_price, unit) VALUES ('كشف', 300, 'per_visit') ON CONFLICT (name) DO NOTHING"
    ))
    print("Migration complete: consultation-fee procedure ('كشف') seeded.")
