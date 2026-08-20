import os
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL not set — check your .env file")

# connect_args timeouts matter as much as pool_pre_ping here: the Supabase pooler can
# silently drop a connection that idled for a while (already seen and fixed once for
# whatsapp_agent/db.py's own connection -- same root cause). Without an explicit
# connect_timeout/keepalives, a stale pooled connection's pre-ping validation can hang
# indefinitely on a half-dead TCP socket instead of failing fast and reconnecting --
# which manifests as every DB-touching request silently stalling forever.
engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    connect_args={
        "connect_timeout": 10,
        "keepalives": 1, "keepalives_idle": 30, "keepalives_interval": 10, "keepalives_count": 3,
    },
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    """FastAPI dependency — one DB session per request, closed automatically."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()