from fastapi import APIRouter
from pydantic import BaseModel

from backend.core.database import get_db

router = APIRouter(prefix="/api/settings", tags=["settings"])

ALLOWED_KEYS = {
    "tool_level",
    "tts_voice",
    "tts_rate",
    "default_model",
    "inference_backend",
    "auto_learn",
    "tts_autoplay",
    "tts_followup",
    "theme",
    "reflection_mode",
    "voice_model",
    "stt_model",
    "telegram_token",
    "telegram_enabled",
    "telegram_model",
    "livekit_url",
    "livekit_api_key",
    "livekit_api_secret",
    "style_auto",
}


class SettingUpdate(BaseModel):
    key: str
    value: str


@router.get("")
async def get_settings() -> dict:
    rows = get_db().query_all("SELECT key, value FROM app_settings")
    return {"settings": {r["key"]: r["value"] for r in rows}}


@router.get("/interests")
async def get_interests() -> dict:
    from backend.core import interests

    selection = interests.selection()
    return {
        "catalogue": interests.catalogue(),
        "selection": selection,
        "configura": bool(selection),
    }


class InterestsUpdate(BaseModel):
    themes: list[str]


@router.put("/interests")
async def put_interests(req: InterestsUpdate) -> dict:
    from backend.core import interests

    valides = interests.definir(req.themes)
    return {"ok": True, "selection": valides}


@router.put("")
async def set_setting(req: SettingUpdate) -> dict:
    if req.key not in ALLOWED_KEYS:
        return {"ok": False, "error": f"Cle non autorisee: {req.key}"}
    db = get_db()
    existing = db.query_one("SELECT key FROM app_settings WHERE key=?", (req.key,))
    if existing:
        db.execute(
            "UPDATE app_settings SET value=? WHERE key=?", (req.value, req.key)
        )
    else:
        db.execute(
            "INSERT INTO app_settings (key, value) VALUES (?,?)", (req.key, req.value)
        )
    return {"ok": True}


def get_setting(key: str, default: str = "") -> str:
    row = get_db().query_one("SELECT value FROM app_settings WHERE key=?", (key,))
    return row["value"] if row else default
