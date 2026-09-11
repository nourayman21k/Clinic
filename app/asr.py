"""Shared local ASR: QwenCleo-ASR (mohammedaly22/QwenCleo-ASR, a Qwen3-ASR-1.7B
fine-tune) running on this machine's GPU in place of Groq's hosted Whisper.
Roughly halves Whisper large-v3's error rate on Egyptian Arabic + Arabic/
English code-switching (WER 19.85 vs 63.94 per the model's own benchmark) --
exactly this clinic's real speech pattern. See
scripts/convert_qwencleo_checkpoint.py for why the checkpoint under
models/qwencleo-asr-converted/ isn't loaded straight from the Hub.

Used by BOTH the doctor's voice-charge dictation (app/voice_charge.py) and
the WhatsApp patient voice notes (app/whatsapp_agent/transcription.py) --
a single shared instance here, not one per caller, so the ~4GB model only
ever occupies VRAM once.

Lazy-loaded (NOT at import time): eagerly loading this model at import time
hangs the whole backend on startup, since voice_charge.py (and therefore
this module) is imported from app/routers/doctor.py at the top level.
Loaded once, on first actual transcription call, and cached for the life
of the process.
"""
import os
import subprocess

import numpy as np
import torch
from imageio_ffmpeg import get_ffmpeg_exe

_ASR_MODEL_ID = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "models", "qwencleo-asr-converted"))
_DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
# bfloat16 matches the model's native training/release precision. Reasonable
# CPU op support too (as a fallback on a machine with no CUDA), unlike float16.
_DTYPE = torch.bfloat16

_model = None
_processor = None


def _get_asr():
    global _model, _processor
    if _model is None:
        from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor

        print(f"[asr] Loading {_ASR_MODEL_ID} (first use, {_DEVICE}, one-time)...")
        _processor = AutoProcessor.from_pretrained(_ASR_MODEL_ID)
        _model = AutoModelForSpeechSeq2Seq.from_pretrained(_ASR_MODEL_ID, dtype=_DTYPE).to(_DEVICE)
        _model.eval()
        print(f"[asr] ASR model ready on {_DEVICE}.")
    return _model, _processor


def _decode_to_pcm16k_mono(audio_bytes: bytes) -> np.ndarray:
    """Decode any container/codec ffmpeg understands (WhatsApp's ogg/opus,
    webm, m4a, ...) to mono float32 PCM at 16kHz -- what the model's
    WhisperFeatureExtractor expects. Piped through ffmpeg's stdin/stdout
    (imageio_ffmpeg's bundled static binary, so this doesn't depend on
    ffmpeg being on PATH), no temp files."""
    proc = subprocess.run(
        [
            get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error",
            "-i", "-", "-f", "f32le", "-ac", "1", "-ar", "16000", "-",
        ],
        input=audio_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg audio decode failed: {proc.stderr.decode(errors='replace')}")
    return np.frombuffer(proc.stdout, dtype=np.float32)


def transcribe(audio_bytes: bytes, language: str = "ar") -> str:
    """Transcribe raw audio bytes (any ffmpeg-decodable container) to text."""
    model, processor = _get_asr()
    waveform = _decode_to_pcm16k_mono(audio_bytes)

    inputs = processor.apply_transcription_request(audio=waveform, language=language)
    # BatchFeature.to() only casts floating-point tensors (audio features) when a dtype is
    # given -- input_ids/attention_mask stay integer regardless, so this is safe to pass together.
    inputs = inputs.to(model.device, dtype=model.dtype)

    with torch.inference_mode():
        generated = model.generate(**inputs, max_new_tokens=256)

    new_tokens = generated[0, inputs["input_ids"].shape[1]:]
    text = processor.decode(new_tokens, return_format="transcription_only")
    return text.strip()
