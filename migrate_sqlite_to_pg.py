"""One-off, idempotent migration: carry the WhatsApp agent's patients and FUTURE
appointments from the old SQLite clinic.db into the dashboard's Postgres, so
returning patients are recognized and upcoming bookings appear on the dashboards.

- SQLite is opened READ-ONLY; it is never modified (it stays as an archive).
- Re-runnable: every insert is ON CONFLICT ... DO NOTHING.
- Historical payments/patient_procedures are deliberately NOT migrated -- they
  predate the dashboard's payment shape and are test data that would pollute
  the analytics.

Run:  .venv\\Scripts\\python.exe migrate_sqlite_to_pg.py
"""
import os
import re
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

import psycopg2
from dotenv import load_dotenv

load_dotenv()
CAIRO = ZoneInfo("Africa/Cairo")

# Real Egyptian mobiles in international form; rows behind a WhatsApp @lid keep
# their (junk-looking but unique) LID-digit phone so cell 46's whatsapp_chat_id
# lookup still recognizes them as returning patients.
EG_PHONE = re.compile(r"^20(10|11|12|15)\d{8}$")
DENYLIST = {
    "201066744255",  # fake test number (old cell 48)
    "201099887766",  # fake test number (old cell 49)
    "201066677788",  # fake demo patient محمد سيد (old say() demos)
    "201017989362",  # the clinic bot's own WhatsApp number
}

sq = sqlite3.connect("file:clinic.db?mode=ro", uri=True)
pg = psycopg2.connect(os.environ["DATABASE_URL"], connect_timeout=10)
cur = pg.cursor()

# --- 1) procedures (belt-and-suspenders with the notebook's own seed cell) ----
seeded = 0
for name, price, unit in sq.execute("SELECT name, base_price, unit FROM procedures"):
    cur.execute(
        "INSERT INTO procedures (name, base_price, unit) VALUES (%s, %s, %s) ON CONFLICT (name) DO NOTHING",
        (name, price, unit),
    )
    seeded += cur.rowcount

# --- 2) patients ---------------------------------------------------------------
rows = sq.execute(
    "SELECT id, name, phone, age, gender, whatsapp_chat_id, created_at FROM patients"
).fetchall()

migrated_patients, skipped_patients = 0, []
eligible = []
for sid, name, phone, age, gender, chat_id, created_at in rows:
    if phone in DENYLIST:
        skipped_patients.append((sid, name, phone, "denylisted test/fake number"))
        continue
    if not (EG_PHONE.match(phone or "") or chat_id):
        skipped_patients.append((sid, name, phone, "no valid EG phone and no whatsapp_chat_id"))
        continue
    eligible.append((sid, name, phone, age, gender, chat_id, created_at))

for sid, name, phone, age, gender, chat_id, created_at in eligible:
    created = None
    if created_at:
        try:  # SQLite CURRENT_TIMESTAMP wrote UTC
            created = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S").replace(tzinfo=ZoneInfo("UTC"))
        except ValueError:
            pass
    if created:
        cur.execute(
            """INSERT INTO patients (name, phone, age, gender, whatsapp_chat_id, created_at)
               VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (phone) DO NOTHING""",
            (name, phone, age, gender, chat_id, created),
        )
    else:
        cur.execute(
            """INSERT INTO patients (name, phone, age, gender, whatsapp_chat_id)
               VALUES (%s, %s, %s, %s, %s) ON CONFLICT (phone) DO NOTHING""",
            (name, phone, age, gender, chat_id),
        )
    migrated_patients += cur.rowcount

# --- 3) sqlite_id -> pg_id map via phone (RETURNING is empty on conflicts) ------
phones = [p for _, _, p, *_ in eligible]
cur.execute("SELECT id, phone FROM patients WHERE phone = ANY(%s)", (phones,))
pg_id_by_phone = {phone: pid for pid, phone in cur.fetchall()}
pg_id_by_sqlite_id = {
    sid: pg_id_by_phone[phone] for sid, _, phone, *_ in eligible if phone in pg_id_by_phone
}

# --- 4) future non-cancelled appointments ---------------------------------------
now_cairo = datetime.now(CAIRO)
appts = sq.execute(
    """SELECT id, patient_id, doctor_id, scheduled_at, status, confirmation_sent
       FROM appointments WHERE status != 'cancelled'"""
).fetchall()

migrated_appts, skipped_appts = 0, []
for aid, spid, doctor_id, scheduled_at, status, confirmation_sent in appts:
    try:  # notebook wrote Cairo wall-clock strings
        when = datetime.strptime(scheduled_at, "%Y-%m-%d %H:%M:%S").replace(tzinfo=CAIRO)
    except (ValueError, TypeError):
        skipped_appts.append((aid, f"unparseable scheduled_at {scheduled_at!r}"))
        continue
    if when < now_cairo:
        continue  # history stays in the SQLite archive
    pg_pid = pg_id_by_sqlite_id.get(spid)
    if not pg_pid:
        skipped_appts.append((aid, f"patient sqlite_id={spid} was not migrated"))
        continue
    cur.execute(
        """INSERT INTO appointments (patient_id, doctor_id, scheduled_at, status, confirmation_sent)
           VALUES (%s, %s, %s, %s, %s)
           ON CONFLICT (doctor_id, scheduled_at) WHERE status != 'cancelled' DO NOTHING""",
        (pg_pid, doctor_id, when, status, bool(confirmation_sent)),
    )
    if cur.rowcount == 0:
        skipped_appts.append((aid, "slot already taken in Postgres (conflict) -- left as-is"))
    else:
        migrated_appts += cur.rowcount

pg.commit()

# --- 5) summary + dashboard-style verification ----------------------------------
print(f"procedures seeded:     {seeded}")
print(f"patients migrated:     {migrated_patients} (of {len(eligible)} eligible, {len(rows)} total in SQLite)")
for sid, name, phone, why in skipped_patients:
    print(f"   skipped patient sqlite_id={sid} {name!r} phone={phone!r}: {why}")
print(f"appointments migrated: {migrated_appts}")
for aid, why in skipped_appts:
    print(f"   skipped appointment sqlite_id={aid}: {why}")

cur.execute("SELECT COUNT(*) FROM patients")
print(f"\nPostgres now: {cur.fetchone()[0]} patients", end="")
cur.execute("SELECT COUNT(*) FROM appointments WHERE status != 'cancelled' AND scheduled_at >= now()")
print(f", {cur.fetchone()[0]} upcoming appointments")
cur.execute(
    """SELECT a.id, p.name, (a.scheduled_at AT TIME ZONE 'Africa/Cairo')::timestamp, a.status
       FROM appointments a JOIN patients p ON p.id = a.patient_id
       WHERE a.status != 'cancelled' AND a.scheduled_at >= now()
       ORDER BY a.scheduled_at LIMIT 30"""
)
print("\nUpcoming (Cairo time), exactly as the dashboards will see them:")
for r in cur.fetchall():
    print("  ", r)
pg.close()
sq.close()
