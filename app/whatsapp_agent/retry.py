import time


def with_retry(fn, attempts: int = 4, base_delay: int = 3):
    """Shared retry helper for transient LLM/API errors (429/500/503). Used by
    both the Groq transcription calls and the agent's own LLM call inside
    agent() (DeepSeek). Retrying INSIDE the single graph.invoke() call matters:
    without it, a transient error bubbles out to the caller, which (in the old
    poll-loop design) re-submitted the whole message fresh on the next cycle --
    if the failed attempt already left partial state behind, that could produce
    a second reply for a message the patient only sent once. The webhook
    dispatcher deliberately does NOT retry a failed message at all (see
    dispatcher.py) precisely to avoid resurrecting that bug, so this in-call
    retry is the only line of defense against transient provider errors."""
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as e:
            msg = str(e).lower()
            transient = "429" in msg or "rate limit" in msg or "500" in msg or "503" in msg
            if not transient or attempt == attempts:
                raise
            delay = base_delay * (2 ** (attempt - 1))
            print(f"   ⏳ LLM rate-limited (attempt {attempt}/{attempts}) — retrying in {delay}s…")
            time.sleep(delay)
