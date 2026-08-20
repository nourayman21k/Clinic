from typing import List, Optional

from langchain_groq import ChatGroq
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .voice_charge import GROQ_API_KEY, find_patients_by_name_db, find_procedure_db, transcribe_audio  # noqa: F401 (transcribe_audio re-exported for callers)

_llm = ChatGroq(model="openai/gpt-oss-120b", temperature=0, api_key=GROQ_API_KEY)


class TreatmentLineItem(BaseModel):
    procedure_name: str = Field(description="Procedure name exactly as the doctor said it, in Egyptian Arabic")
    tooth_area: Optional[str] = Field(default=None, description="Tooth number or area mentioned, else null")
    is_general: bool = Field(default=False, description="True if this item is not tied to one specific tooth (e.g. a cleaning)")
    notes: Optional[str] = Field(default=None, description="Any extra note the doctor gave, e.g. sequencing ('start with the filling')")


class TreatmentExtractionResult(BaseModel):
    patient_name: Optional[str] = Field(default=None, description="Patient's name, null if not mentioned")
    items: List[TreatmentLineItem] = Field(default_factory=list)
    ambiguous: bool = Field(description="True if the patient name is missing/unclear")
    clarification_needed: Optional[str] = Field(
        default=None, description="Short Arabic question to ask the doctor if something is unclear, else null"
    )


_extractor = _llm.with_structured_output(TreatmentExtractionResult)

EXTRACTION_PROMPT = """You are a dental treatment-plan extractor for an Egyptian clinic.
The doctor describes, in Egyptian Arabic dialect, what a patient needs done in future visits
(not what was just performed -- this is a PLAN, not a completed charge).
Extract the patient's name and each treatment item: the procedure, the tooth number/area if
mentioned, whether it's general (not tooth-specific, e.g. cleaning) or tooth-specific, and any note.
If the patient's name is missing, set ambiguous=true and ask for it in clarification_needed."""


def build_draft(db: Session, transcript: str) -> dict:
    """Turn a transcribed doctor utterance into a draft treatment plan: resolves
    the patient and each procedure against the real database. Read-only, no DB
    writes; the doctor confirms/edits and posts via
    POST /api/doctor/treatment-plan."""
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
        "low_confidence": not result.items,
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

    for item in result.items:
        proc = find_procedure_db(db, item.procedure_name)
        draft["lines"].append({
            "matched": proc is not None,
            "procedure_id": proc.id if proc else None,
            "name": proc.name if proc else None,
            "tooth_area": None if item.is_general else item.tooth_area,
            "is_general": item.is_general,
            "notes": item.notes,
            "spoken_name": item.procedure_name,
        })

    return draft
