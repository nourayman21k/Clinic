import os
import threading
from datetime import datetime

import psycopg2
import psycopg2.errors

from .config import CAIRO

# The SAME Postgres the doctor/secretary dashboards read (DATABASE_URL in .env --
# Supabase transaction pooler). autocommit=True is deliberate and load-bearing:
# psycopg2 otherwise opens an implicit transaction on the FIRST statement of any
# kind (even a SELECT) and holds it until commit -- which, through a transaction
# pooler, pins a pooled server backend "idle in transaction" for however long the
# clinic goes between WhatsApp messages. In autocommit every statement is atomic
# on its own and the partial unique index on appointments stays the double-booking
# guard.
#
# `conn` below is a THREAD-LOCAL connection (module __getattr__, PEP 562), not a
# single shared one: the dispatcher runs up to WHATSAPP_AGENT_CONCURRENCY messages
# concurrently on separate worker threads (asyncio.to_thread), and a single
# psycopg2 connection is not safe to use from multiple threads at once. Every
# other module in this package accesses it as `db.conn` (module attribute access,
# never `from .db import conn`), so this stays transparent -- each thread just
# gets its own connection the first time it touches `db.conn`.

_local = threading.local()


def _connect():
    return psycopg2.connect(
        os.environ["DATABASE_URL"],
        connect_timeout=10,
        keepalives=1, keepalives_idle=30, keepalives_interval=10, keepalives_count=3,
    )


def _thread_conn():
    if not hasattr(_local, "conn"):
        c = _connect()
        c.autocommit = True
        _local.conn = c
    return _local.conn


def __getattr__(name):
    if name == "conn":
        return _thread_conn()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def ensure_conn():
    """The pooler/NAT can silently drop a connection that idled for hours between
    patients. Called at the top of every webhook delivery: cheap ping, reconnect
    if dead (for THIS thread's connection only)."""
    c = _thread_conn()
    try:
        cur = c.cursor()
        cur.execute("SELECT 1")
        cur.fetchone()
    except (psycopg2.OperationalError, psycopg2.InterfaceError):
        print("   🔌 DB connection lost -- reconnecting...")
        try:
            c.close()
        except Exception:
            pass
        _local.conn = _connect()
        _local.conn.autocommit = True


def now_cairo():
    """Cairo-aware timestamp for every write. The server session runs in UTC, so a
    naive datetime here would be stored 2-3h off and shift the dashboards' "today"."""
    return datetime.now(CAIRO)
