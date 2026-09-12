import os
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

CAIRO = ZoneInfo("Africa/Cairo")

OPENWA_URL = os.getenv("OPENWA_URL")
OPENWA_API_KEY = os.getenv("OPENWA_API_KEY")
OPENWA_SESSION_ID = os.getenv("OPENWA_SESSION_ID")

GROQ_API_KEY = os.getenv("GROQ_API_KEY")

WHATSAPP_WEBHOOK_SECRET = os.getenv("WHATSAPP_WEBHOOK_SECRET")
WHATSAPP_AGENT_CONCURRENCY = int(os.getenv("WHATSAPP_AGENT_CONCURRENCY", "5"))

# Per-patient abuse guard: caps how many incoming messages from the SAME chat the
# LLM agent will answer within one hour. The hour starts when that chat sends its
# FIRST message; once the cap is reached the agent goes silent for the rest of that
# hour, then the allowance resets (see rate_limit.py). Anything past the cap is
# dropped before the agent runs -- no LLM call, no token cost, no reply.
# Not a security boundary -- in-memory, resets on restart, same as dispatcher.py's
# own per-chat locks -- just a cheap cap on runaway spend.
WHATSAPP_MAX_MESSAGES_PER_HOUR = int(os.getenv("WHATSAPP_MAX_MESSAGES_PER_HOUR", "5"))

# Where OpenWA should POST webhook deliveries. OpenWA runs inside a Docker
# container (Docker Desktop), so "127.0.0.1"/"localhost" would resolve to the
# CONTAINER's own loopback, not this host -- host.docker.internal is Docker
# Desktop's special DNS name for reaching the host machine from inside a
# container. Override if the backend ever runs somewhere else reachable only
# via a tunnel/public URL.
BACKEND_PUBLIC_URL = os.getenv("BACKEND_PUBLIC_URL", "http://host.docker.internal:8001")

# The clinic's own WhatsApp number -- never process a message "from" ourselves
# (mirrors the notebook's poll-loop filter).
MY_OWN_NUMBER = "201017989362"

CLINIC_LATITUDE = 30.005726365904355
CLINIC_LONGITUDE = 31.468899256922267
CLINIC_MAPS_LINK = f"https://www.google.com/maps?q={CLINIC_LATITUDE},{CLINIC_LONGITUDE}"

CLINIC_HOURS = ["09:00", "09:30", "10:00", "10:30", "11:00", "11:30", "12:00", "12:30",
                "13:00", "13:30", "14:00", "14:30", "15:00", "16:00", "17:00"]

# Patients can only book within this many days from today -- enforced at the
# tool layer, not just the prompt, so a misbehaving model can never book past it.
BOOKING_WINDOW_DAYS = 14
