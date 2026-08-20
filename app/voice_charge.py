import os
import re
from typing import List, Optional

from dotenv import load_dotenv
from groq import Groq
from langchain_groq import ChatGroq
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from langchain_huggingface import ChatHuggingFace
from .models import Patient, Procedure

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
if not GROQ_API_KEY:
    raise RuntimeError("GROQ_API_KEY not set — check your .env file")

_groq_client = Groq(api_key=GROQ_API_KEY)
_llm = ChatGroq(model="openai/gpt-oss-120b", temperature=0, api_key=GROQ_API_KEY)

MIN_SUBSTRING_MATCH_LEN = 3


from transformers import AutoProcessor, AutoModelForSpeechSeq2Seq
import torch

model_id = "mohammedaly22/QwenCleo-ASR"

processor = AutoProcessor.from_pretrained(model_id)
model = AutoModelForSpeechSeq2Seq.from_pretrained(
    model_id,
    torch_dtype=torch.float16,
    device_map="auto"
)





class ProcedureItem(BaseModel):
    name: str = Field(description="Procedure name exactly as the doctor said it, in Egyptian Arabic")
    quantity: int = Field(default=1, description="How many teeth/sessions were done")
    tooth_area: Optional[str] = Field(default=None, description="Tooth or area mentioned, if any")


class ExtractionResult(BaseModel):
    patient_name: Optional[str] = Field(default=None, description="Patient's name, null if not mentioned")
    procedures: List[ProcedureItem] = Field(default_factory=list)
    ambiguous: bool = Field(description="True if patient name is missing/unclear")
    clarification_needed: Optional[str] = Field(
        default=None, description="Short Arabic question to ask the doctor if something is unclear, else null"
    )


_extractor = _llm.with_structured_output(ExtractionResult)

EXTRACTION_PROMPT = """You are a dental procedure extractor for an Egyptian clinic.
The doctor describes procedures they just performed, in Egyptian Arabic dialect.
Extract the patient's name and each procedure with its quantity and tooth area if mentioned.
If the patient's name is missing, set ambiguous=true and ask for it in clarification_needed.
If a procedure name is too vague to price, set clarification_needed to a short Arabic question."""


def normalize_arabic(text: str, strip_al: bool = True) -> str:
    """Normalize Arabic text for matching: strip diacritics, unify hamza forms,
    optionally remove 'ال' (the) prefix from words, collapse extra spaces.
    strip_al is off for patient NAMES — stripping 'ال' from names mangles ones
    that legitimately start with it (الاء -> اء, السيد -> سيد)."""
    text = re.sub(r"[ً-ْ]", "", text)  # remove diacritics (tashkeel)
    text = text.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
    text = text.replace("ة", "ه")
    if strip_al:
        text = re.sub(r"\bال", "", text)  # strip "ال" prefix from each word
    text = re.sub(r"\s+", " ", text).strip()
    return text


def find_procedure_db(db: Session, name: str) -> Optional[Procedure]:
    """Exact-normalized match first; only fall back to substring matching,
    and only when the search term is long enough to be meaningful."""
    normalized_search = normalize_arabic(name)
    procs = db.query(Procedure).all()

    for p in procs:  # pass 1: exact
        if normalized_search == normalize_arabic(p.name):
            return p

    if len(normalized_search) >= MIN_SUBSTRING_MATCH_LEN:  # pass 2: substring
        for p in procs:
            normalized_proc = normalize_arabic(p.name)
            if normalized_search in normalized_proc or normalized_proc in normalized_search:
                return p
    return None


def find_patients_by_name_db(db: Session, name: str) -> List[Patient]:
    """Names keep their 'ال' (strip_al=False); exact matches win outright,
    substring fallback requires a minimum length. Returns 0, 1, or many rows —
    callers must handle the ambiguous (many) case explicitly."""
    normalized_search = normalize_arabic(name, strip_al=False)
    patients = db.query(Patient).all()

    exact = [p for p in patients if normalize_arabic(p.name, strip_al=False) == normalized_search]
    if exact:
        return exact

    if len(normalized_search) < MIN_SUBSTRING_MATCH_LEN:
        return []
    matches = []
    for p in patients:
        normalized_name = normalize_arabic(p.name, strip_al=False)
        if normalized_search in normalized_name or normalized_name in normalized_search:
            matches.append(p)
    return matches


def transcribe_audio(audio_bytes: bytes, filename: str = "audio.webm") -> str:
    """Transcribe a doctor's voice note to Arabic text using Groq's Whisper large-v3."""
    result = _groq_client.audio.transcriptions.create(
        file=(filename, audio_bytes),
        model=model,
        language="ar",
        response_format="text",
    )
    text = result if isinstance(result, str) else result.text
    return text.strip()


def build_draft(db: Session, transcript: str) -> dict:
    """Turn a transcribed doctor utterance into a draft charge: resolves the
    patient and each procedure against the real database, and always prices
    from the real Procedure catalogue — never from the LLM. Read-only, no
    DB writes; the doctor confirms/edits and posts via the existing
    POST /api/doctor/charges endpoint."""
    result = _extractor.invoke([
        {"role": "system", "content": EXTRACTION_PROMPT},
        {"role": "user", "content": transcript},
    ])

    draft = {
        "transcript": transcript,
        "patient_name_heard": result.patient_name,
        "patient_status": "ok",  # ok | missing | not_found | ambiguous
        "patient": None,
        "patient_candidates": [],
        "lines": [],
        "clarification": result.clarification_needed,
        "low_confidence": not result.procedures,
    }

    if result.ambiguous or not result.patient_name:
        draft["patient_status"] = "missing"
    else:
        matches = find_patients_by_name_db(db, result.patient_name)
        if not matches:
            draft["patient_status"] = "not_found"
        elif len(matches) == 1:
            p = matches[0]
            draft["patient"] = {"id": p.id, "name": p.name, "phone": p.phone}
        else:
            draft["patient_status"] = "ambiguous"
            draft["patient_candidates"] = [{"id": p.id, "name": p.name, "phone": p.phone} for p in matches]

    for item in result.procedures:
        proc = find_procedure_db(db, item.name)
        draft["lines"].append({
            "matched": proc is not None,
            "procedure_id": proc.id if proc else None,
            "name": proc.name if proc else None,
            "unit_price": float(proc.base_price) if proc else None,
            "quantity": item.quantity,
            "tooth_area": item.tooth_area,
            "spoken_name": item.name,
        })

    return draft
