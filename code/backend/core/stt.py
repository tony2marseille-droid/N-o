from pathlib import Path

from backend.core.config import settings

_engine = None
_engine_size = ""


def _size() -> str:
    row = None
    try:
        from backend.core.database import get_db

        row = get_db().query_one(
            "SELECT value FROM app_settings WHERE key='stt_model'"
        )
    except Exception:
        pass
    return (row["value"] if row and row["value"] else "small").strip()


def _load() -> object:
    global _engine, _engine_size
    size = _size()
    if _engine is None or _engine_size != size:
        _engine = None
        from faster_whisper import WhisperModel

        _engine = WhisperModel(size, device="auto", compute_type="int8")
        _engine_size = size
    return _engine


def stt_available() -> bool:
    try:
        import faster_whisper  # noqa: F401

        return True
    except Exception:
        return False


def transcribe(path: Path) -> dict:
    """Transcrit un fichier audio. Renvoie {text, language, duration}."""
    if not path.exists():
        return {"text": "", "language": "", "duration": 0.0}
    segments, info = _load().transcribe(
        str(path), language=None, vad_filter=True, beam_size=5
    )
    parts = [seg.text.strip() for seg in segments]
    return {
        "text": " ".join(p for p in parts if p),
        "language": getattr(info, "language", "") or "",
        "duration": float(getattr(info, "duration", 0.0) or 0.0),
    }