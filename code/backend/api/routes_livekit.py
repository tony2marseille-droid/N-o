import base64
import hashlib
import hmac
import json
import time
import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter
from pydantic import BaseModel

from backend.api.routes_settings import get_setting
from backend.core.database import get_db

router = APIRouter(prefix="/api/livekit", tags=["livekit"])

_KEY_URL = "livekit_url"
_KEY_KEY = "livekit_api_key"
_KEY_SECRET = "livekit_api_secret"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _sign(secret: str, data: bytes) -> str:
    return _b64url(hmac.new(secret.encode(), data, hashlib.sha256).digest())


def _jwt(payload: dict, key: str, secret: str) -> str:
    header = {"alg": "HS256", "typ": "JWT", "kid": key}
    h = _b64url(json.dumps(header, separators=(",", ":")).encode())
    p = _b64url(json.dumps(payload, separators=(",", ":")).encode())
    return f"{h}.{p}.{_sign(secret, f'{h}.{p}'.encode())}"


def _config() -> dict:
    return {
        "url": get_setting(_KEY_URL, ""),
        "api_key": get_setting(_KEY_KEY, ""),
        "api_secret": get_setting(_KEY_SECRET, ""),
    }


class LiveKitTokenReq(BaseModel):
    room: str = "localai"
    identity: str = "assistant"
    ttl_seconds: int = 3600


class LiveKitConfig(BaseModel):
    url: str = ""
    api_key: str = ""
    api_secret: str = ""


@router.get("/status")
async def livekit_status() -> dict:
    cfg = _config()
    return {
        "configured": bool(cfg["url"] and cfg["api_key"] and cfg["api_secret"]),
        "url": cfg["url"],
        "has_api_key": bool(cfg["api_key"]),
        "has_api_secret": bool(cfg["api_secret"]),
    }


@router.put("/config")
async def livekit_configure(req: LiveKitConfig) -> dict:
    db = get_db()
    for key, value in ((_KEY_URL, req.url.strip()), (_KEY_KEY, req.api_key.strip()), (_KEY_SECRET, req.api_secret.strip())):
        if not value:
            continue
        if db.query_one("SELECT key FROM app_settings WHERE key=?", (key,)):
            db.execute("UPDATE app_settings SET value=? WHERE key=?", (value, key))
        else:
            db.execute("INSERT INTO app_settings (key, value) VALUES (?,?)", (key, value))
    return {"ok": True}


@router.post("/token")
async def livekit_token(req: LiveKitTokenReq) -> dict:
    cfg = _config()
    if not (cfg["api_key"] and cfg["api_secret"]):
        return {"ok": False, "error": "Cle API / secret non configures (voir /api/livekit/config)"}
    now = int(time.time())
    payload = {
        "exp": now + req.ttl_seconds,
        "nbf": now,
        "iat": now,
        "iss": cfg["api_key"],
        "sub": cfg["api_key"],
        "jti": uuid.uuid4().hex,
        "name": req.identity,
        "video": {
            "room": req.room,
            "canPublish": True,
            "canSubscribe": True,
            "canPublishData": True,
            "roomCreate": True,
        },
    }
    token = _jwt(payload, cfg["api_key"], cfg["api_secret"])
    return {"ok": True, "token": token, "url": cfg["url"]}


@router.get("/token")
async def livekit_token_get(room: str = "localai", identity: str = "assistant") -> dict:
    return await livekit_token(LiveKitTokenReq(room=room, identity=identity))