import os
import getpass
from dotenv import load_dotenv
from app.database import SessionLocal
from app.models import User
from app.auth import hash_password

load_dotenv()

def create_user(db, role):
    print(f"\n--- Create {role} account ---")
    username = input(f"{role} username: ").strip()
    full_name = input(f"{role} full name: ").strip()
    password = getpass.getpass(f"{role} password (hidden): ")

    existing = db.query(User).filter(User.username == username).first()
    if existing:
        print(f"User '{username}' already exists — skipping.")
        return

    user = User(
        username=username,
        password_hash=hash_password(password),
        role=role,
        full_name=full_name,
    )
    db.add(user)
    db.commit()
    print(f"Created {role} '{username}'.")

if __name__ == "__main__":
    db = SessionLocal()
    try:
        create_user(db, "doctor")
        create_user(db, "secretary")
    finally:
        db.close()