"""Sample classification API endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.api.deps import OptionalAuthUser
from app.api.instruments.common import MAX_SAMPLE_BYTES

router = APIRouter()


@router.post("/classify")
async def classify_sample(
    file: UploadFile = File(...),
    user: OptionalAuthUser = None,
) -> dict:
    """Classify a single audio sample using spectral analysis.

    Returns the detected drum type, GM MIDI note, confidence score,
    and extracted features. Useful for previewing classification before
    uploading a full library.
    """
    from app.services.sample_classifier_service import SampleClassifierService

    content = await file.read()
    if len(content) > MAX_SAMPLE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"sample exceeds {MAX_SAMPLE_BYTES} bytes",
        )
    classifier = SampleClassifierService()
    result = classifier.classify_bytes(content, file.filename or "sample.wav")

    if result is None:
        raise HTTPException(status_code=400, detail="could not classify sample")

    return {
        "filename": file.filename,
        "drum_type": result.drum_type,
        "drum_type_label": classifier.get_drum_type_label(result.drum_type),
        "midi_note": result.midi_note,
        "confidence": round(result.confidence, 4),
        "features": {k: round(v, 4) for k, v in result.features.items()},
    }


@router.get("/drum-types")
def list_drum_types() -> list[dict]:
    """Return all supported drum types with their GM notes and labels."""
    from app.services.sample_classifier_service import SampleClassifierService

    classifier = SampleClassifierService()
    return [
        {"drum_type": drum_type, "midi_note": midi_note, "label": label}
        for drum_type, midi_note, label in classifier.get_all_drum_types()
    ]