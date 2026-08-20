from sqlalchemy import text
from app.database import engine

with engine.begin() as conn:
    conn.execute(text("ALTER TABLE payments ADD COLUMN IF NOT EXISTS receipt_path VARCHAR"))
    print("Migration complete: payments.receipt_path is ready.")
