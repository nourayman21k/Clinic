import time
from collections import defaultdict, deque

from .config import WHATSAPP_MAX_MESSAGES_PER_HOUR

_WINDOW_SECONDS = 3600

# One deque of message timestamps per chat -- a sliding hour window, not a fixed
# clock-hour bucket, so "15 in the last hour" is true at any instant, not just
# reset on the hour. In-memory and per-process: resets on a backend restart, same
# tradeoff dispatcher.py already makes for its per-chat locks/semaphore.
_message_times: dict[str, deque] = defaultdict(deque)


def check_and_record(chat_id: str) -> bool:
    """Returns True (and records this message) if `chat_id` is still under the
    hourly cap; returns False (without recording) if they've hit it. The caller
    should skip the LLM agent entirely on False -- a blocked message costs nothing."""
    now = time.monotonic()
    times = _message_times[chat_id]
    while times and now - times[0] > _WINDOW_SECONDS:
        times.popleft()
    if len(times) >= WHATSAPP_MAX_MESSAGES_PER_HOUR:
        return False
    times.append(now)
    return True
