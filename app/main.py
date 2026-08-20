import sys
from contextlib import asynccontextmanager

# Windows' console codepage (e.g. cp1256) can't encode the emoji/Arabic text used
# throughout the WhatsApp agent's logging -- an un-encodable print() crashes
# whatever thread it runs on (including the whatsapp_agent background tasks,
# silently dropping a patient's message before it's even processed). Force
# UTF-8 on stdout/stderr process-wide; worker threads share this same stream.
# line_buffering=True: stdout is block-buffered (not line-buffered) whenever it
# isn't a real terminal -- e.g. piped to a log file/process supervisor, which is
# how this backend actually runs -- so plain print() output would otherwise sit
# unflushed indefinitely, hiding it from anyone tailing the log during an incident.
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
sys.stderr.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .routers import auth, doctor, secretary
from .receipts import UPLOADS_DIR
from .scheduler import start_scheduler, shutdown_scheduler
from .whatsapp_agent.registration import register_openwa_webhook
from .whatsapp_agent.webhook import router as whatsapp_webhook_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    start_scheduler()
    register_openwa_webhook()
    yield
    shutdown_scheduler()


app = FastAPI(title="Egyptian Clinic API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(doctor.router)
app.include_router(secretary.router)
app.include_router(whatsapp_webhook_router)
app.mount("/uploads", StaticFiles(directory=UPLOADS_DIR), name="uploads")


@app.get("/")
def root():
    return {"status": "ok"}