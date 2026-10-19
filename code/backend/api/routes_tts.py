from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from backend.api.schemas import TTSRequest
from backend.core.config import settings
from backend.tts.engine import (
    TTSEngineError,
    cleanup_cache,
    list_voices,
    new_wav_path,
    synthesize_to_wav,
)

router = APIRouter(prefix="/api/tts", tags=["tts"])


@router.get("/voices")
async def voices() -> dict:
    try:
        return {"voices": list_voices()}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"TTS indisponible: {exc}") from exc


@router.post("")
async def synthesize(req: TTSRequest):
    voice = req.voice or settings.tts_voice
    if not voice:
        try:
            from backend.tts.engine import voix_feminine_fr

            voice = voix_feminine_fr()
        except Exception:
            voice = ""
    rate = req.rate or settings.tts_rate
    out_path = new_wav_path(settings.tts_cache_dir)
    try:
        path = synthesize_to_wav(req.text, out_path, voice_id=voice, rate=rate)
        cleanup_cache(settings.tts_cache_dir)
    except TTSEngineError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return FileResponse(path, media_type="audio/wav", filename=path.name)
