"""Test harness: a throwaway Postgres database, never the real one.

Uses TEST_DATABASE_URL (default: a `clinic_test` database on a local Postgres
at localhost:5433 -- e.g. `docker run -d --name clinic-test-db -e
POSTGRES_PASSWORD=devpass -p 5433:5432 postgres:17`). The schema is rebuilt
from the models once per session, and every table is truncated before each test.

Safety: refuses to run unless the URL points at localhost and a database whose
name ends in `_test`, blanks the OpenWA settings BEFORE the app is imported so
no code path can reach the real WhatsApp session, and never loads the System 1
router model (router tests use a fake decider).
"""
import os
import sys
from urllib.parse import urlparse

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://postgres:devpass@localhost:5433/clinic_test"
)
_parsed = urlparse(TEST_DATABASE_URL)
if _parsed.hostname not in ("localhost", "127.0.0.1") or not _parsed.path.rstrip("/").endswith("_test"):
    raise RuntimeError(f"Refusing to run tests against {TEST_DATABASE_URL!r}: use a local *_test database.")

# Must happen before ANY app import: load_dotenv() never overrides variables
# that are already set, so these win over .env.
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
for var in ("OPENWA_URL", "OPENWA_API_KEY", "OPENWA_SESSION_ID"):
    os.environ[var] = ""
os.environ["WHATSAPP_AGENT_USE_JEV"] = "0"
os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("GROQ_API_KEY", "test-key-not-used")

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import psycopg2  # noqa: E402
import pytest  # noqa: E402
from sqlalchemy import text  # noqa: E402


def _ensure_test_database():
    admin = psycopg2.connect(TEST_DATABASE_URL.rsplit("/", 1)[0] + "/postgres", connect_timeout=10)
    admin.autocommit = True
    name = _parsed.path.lstrip("/")
    cur = admin.cursor()
    cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,))
    if not cur.fetchone():
        cur.execute(f'CREATE DATABASE "{name}"')
    admin.close()


_ensure_test_database()

from app import whatsapp  # noqa: E402
from app.auth import create_access_token, hash_password  # noqa: E402
from app.database import Base, SessionLocal, engine  # noqa: E402
from app.models import Doctor, Patient, Procedure, User  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def schema():
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        # The live database's double-booking guard (book_appointment relies on it).
        conn.execute(text(
            "CREATE UNIQUE INDEX uq_appt_doctor_slot ON appointments (doctor_id, scheduled_at) WHERE status <> 'cancelled'"
        ))
        conn.execute(text("CREATE TABLE failed_whatsapp_sends (id SERIAL PRIMARY KEY, chat_id TEXT, text TEXT, created_at TIMESTAMPTZ DEFAULT now())"))
        conn.execute(text("CREATE TABLE processed_whatsapp_messages (idempotency_key TEXT PRIMARY KEY, created_at TIMESTAMPTZ DEFAULT now())"))
    yield
    engine.dispose()


@pytest.fixture(autouse=True)
def clean_db(schema):
    tables = ", ".join(Base.metadata.tables) + ", failed_whatsapp_sends, processed_whatsapp_messages"
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    yield


@pytest.fixture(autouse=True)
def no_real_whatsapp(monkeypatch):
    """Patient sends are recorded, and any real HTTP call through `requests`
    (OpenWA's client) fails the test."""
    sent = []
    monkeypatch.setattr(whatsapp, "send_whatsapp_text", lambda patient, text_: sent.append((patient.id, text_)) or True)
    monkeypatch.setattr(whatsapp, "send_whatsapp_document", lambda *a, **k: True)
    import requests

    def _blocked(*args, **kwargs):
        raise AssertionError(f"Test tried to make a real HTTP call: {args[:1]}")

    monkeypatch.setattr(requests, "post", _blocked)
    monkeypatch.setattr(requests, "get", _blocked)
    return sent


@pytest.fixture
def db():
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture
def seed(db):
    """Doctor + secretary accounts, the doctor profile, a few catalogue procedures and one patient."""
    doctor_user = User(username="doc", password_hash=hash_password("x"), role="doctor", full_name="Dr. Test")
    secretary_user = User(username="sec", password_hash=hash_password("x"), role="secretary", full_name="Sec")
    db.add_all([doctor_user, secretary_user])
    db.flush()
    doctor = Doctor(name="Dr. Test", user_id=doctor_user.id, is_active=True)
    filling = Procedure(name="حشو", base_price=200, unit="per_tooth")
    consult = Procedure(name="كشف", base_price=300, unit="per_visit")
    patient = Patient(name="أحمد محمود", phone="201111111111", whatsapp_opt_in=True)
    db.add_all([doctor, filling, consult, patient])
    db.commit()
    return {"doctor_user": doctor_user, "secretary_user": secretary_user, "doctor": doctor,
            "filling": filling, "consult": consult, "patient": patient}


@pytest.fixture
def doctor_headers(seed):
    return {"Authorization": f"Bearer {create_access_token({'sub': str(seed['doctor_user'].id)})}"}
