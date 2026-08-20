from sqlalchemy import text
from app.database import engine

# These 4 tables were added across this project's active development and, unlike
# the original schema (patients/appointments/payments/users/etc., which already
# had RLS enabled), never had Row Level Security turned on -- flagged by
# Supabase's security advisor. The backend connects as the `postgres` role
# (table owner, rolbypassrls=true), so RLS never applies to it -- this only
# closes Supabase's auto-generated public REST API surface for these tables,
# with zero effect on the app itself.
TABLES = ["analytics_snapshots", "failed_whatsapp_sends", "processed_whatsapp_messages", "treatment_items"]

with engine.begin() as conn:
    for table in TABLES:
        conn.execute(text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
        print(f"RLS enabled on {table}.")
