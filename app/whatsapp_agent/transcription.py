import base64
import os
import tempfile

from groq import Groq

from .config import GROQ_API_KEY
from .retry import with_retry

groq_client = Groq(api_key=GROQ_API_KEY)


def transcribe_audio(file_path: str) -> str:
    """Transcribe an audio file to Arabic text using Groq's Whisper large-v3."""
    def _call():
        with open(file_path, "rb") as f:
            return groq_client.audio.transcriptions.create(
                file=f,
                model="whisper-large-v3",
                language="ar",
                response_format="json",
                temperature=0.0,
            )
    return with_retry(_call).text


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
