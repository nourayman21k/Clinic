from sqlalchemy import text
from app.database import engine

with engine.begin() as conn:
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS processed_whatsapp_messages (
            idempotency_key TEXT PRIMARY KEY,
            processed_at TIMESTAMPTZ DEFAULT now()
        )
    """))
    print("Migration complete: processed_whatsapp_messages table is ready.")
