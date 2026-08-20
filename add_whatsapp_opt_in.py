from sqlalchemy import text
from app.database import engine

with engine.begin() as conn:
    conn.execute(text(
        "ALTER TABLE patients ADD COLUMN IF NOT EXISTS whatsapp_opt_in BOOLEAN DEFAULT TRUE"
    ))
    # Existing rows predate this column's default -- backfill explicitly so
    # nobody is accidentally NULL (which the opt-in check would need to treat
    # as "opted in" anyway, but an explicit TRUE is clearer than relying on that).
    conn.execute(text(
        "UPDATE patients SET whatsapp_opt_in = TRUE WHERE whatsapp_opt_in IS NULL"
    ))
    print("Migration complete: patients.whatsapp_opt_in is ready (defaults TRUE, backfilled).")
