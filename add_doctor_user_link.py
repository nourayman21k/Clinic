from sqlalchemy import text
from app.database import engine, SessionLocal
from app.models import User, Doctor

with engine.begin() as conn:
    conn.execute(text(
        "ALTER TABLE doctors ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id)"
    ))
    print("Migration complete: doctors.user_id is ready.")

    conn.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_appt_doctor_slot "
        "ON appointments (doctor_id, scheduled_at) WHERE status != 'cancelled'"
    ))
    print("Migration complete: double-booking guard index is ready.")

db = SessionLocal()
try:
    doctor_user = db.query(User).filter(User.role == "doctor").first()
    if doctor_user:
        existing = db.query(Doctor).filter(Doctor.user_id == doctor_user.id).first()
        if not existing:
            db.add(Doctor(
                name=doctor_user.full_name or doctor_user.username,
                specialty="General Dentistry",
                is_active=True,
                user_id=doctor_user.id,
            ))
            db.commit()
            print(f"Seeded Doctor row linked to user '{doctor_user.username}' (id={doctor_user.id}).")
        else:
            print(f"Doctor row already linked to user '{doctor_user.username}' — skipping seed.")
    else:
        print("No user with role='doctor' found — nothing to seed.")
finally:
    db.close()
