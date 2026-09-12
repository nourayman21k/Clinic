import time

from .config import WHATSAPP_MAX_MESSAGES_PER_HOUR

_WINDOW_SECONDS = 3600

# {chat_id: (window_started_at, messages_answered_in_window)}
#
# The hour is anchored to the chat's FIRST message, not sliding: once a chat uses
# up its allowance the agent stays silent for the REST of that hour, then the whole
# allowance resets at once. (A sliding window would instead hand back one slot at a
# time as old messages aged out, letting a spammer trickle on indefinitely.)
#
# In-memory and per-process: resets on a backend restart, the same tradeoff
# dispatcher.py already makes for its per-chat locks/semaphore.
_windows: dict[str, tuple[float, int]] = {}


def check_and_record(chat_id: str) -> bool:
    """Returns True (and counts this message) if `chat_id` may still be answered
    this hour; False once it has used up WHATSAPP_MAX_MESSAGES_PER_HOUR. On False
    the caller must skip the agent entirely -- a blocked message costs nothing."""
    now = time.monotonic()
    started_at, count = _windows.get(chat_id, (None, 0))

    if started_at is None or now - started_at >= _WINDOW_SECONDS:
        _windows[chat_id] = (now, 1)  # first message of a fresh hour
        return True

    if count >= WHATSAPP_MAX_MESSAGES_PER_HOUR:
        return False  # silent for the remainder of this hour

    _windows[chat_id] = (started_at, count + 1)
    return True
