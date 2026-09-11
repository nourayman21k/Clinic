import base64
import os
import tempfile

from .. import asr
from .retry import with_retry


def transcribe_audio(file_path: str) -> str:
    """Transcribe an audio file to Arabic text using the local QwenCleo-ASR
    model on GPU (see app/asr.py) -- shared with the doctor's voice-charge
    dictation, so the model is only ever loaded once, process-wide."""
    def _call():
        with open(file_path, "rb") as f:
            return asr.transcribe(f.read(), language="ar")
    return with_retry(_call)


def save_and_transcribe_voice(media_data_base64: str, mimetype: str) -> str:
    ext = "ogg" if "ogg" in mimetype else "m4a"
    audio_bytes = base64.b64decode(media_data_base64)
    with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False) as f:
        f.write(audio_bytes)
        temp_path = f.name
    try:
        return transcribe_audio(temp_path)
    finally:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
