import asyncio
import json
import logging
from datetime import datetime

import httpx
from fastapi import APIRouter
from pydantic import BaseModel, Field

from backend.api.routes_settings import get_setting
from backend.core.database import get_db
from backend.inference import get_active_backend

router = APIRouter(prefix="/api/telegram", tags=["telegram"])

_log = logging.getLogger("telegram")
_KEY_TOKEN = "telegram_token"
_KEY_ENABLED = "telegram_enabled"
_KEY_MODEL = "telegram_model"

_poller: asyncio.Task | None = None
_last_error = ""
_sessions: dict[int, int] = {}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _token() -> str:
    return get_setting(_KEY_TOKEN, "").strip()


def _enabled() -> bool:
    return get_setting(_KEY_ENABLED, "1") == "1"


def _model() -> str:
    return get_setting(_KEY_MODEL, "") or get_setting("voice_model", "gemma3:4b")


async def _send(bot: str, chat_id: int, text: str) -> None:
    async with httpx.AsyncClient(timeout=30) as client:
        await client.post(
            f"https://api.telegram.org/bot{bot}/sendMessage",
            json={"chat_id": chat_id, "text": text[:4000]},
        )


async def _handle_message(bot: str, chat_id: int, text: str) -> None:
    db = get_db()
    text = text.strip()
    if text.startswith("/"):
        cmd = text.split()[0].lower()
        if cmd in ("/start", "/help"):
            await _send(bot, chat_id, "Salut. Envoie une question, je reponds de facon adaptative.")
            return
        if cmd in ("/new", "/nouveau", "/clear", "/reset"):
            _sessions.pop(chat_id, None)
            db.execute(
                "DELETE FROM telegram_sessions WHERE chat_id=?", (chat_id,)
            )
            await _send(bot, chat_id, "Nouvelle conversation.")
            return
        if cmd == "/sum":
            row = db.query_one(
                "SELECT c.summary FROM telegram_sessions s JOIN conversations c"
                " ON c.id=s.conversation_id WHERE s.chat_id=?",
                (chat_id,),
            )
            summary = (row["summary"] if row else "") or "aucun resume"
            await _send(bot, chat_id, f"Resume actuel: {summary[:1500]}")
            return

    backend = get_active_backend()
    conv_id = _sessions.get(chat_id)
    if conv_id is None:
        row = db.query_one(
            "SELECT conversation_id FROM telegram_sessions WHERE chat_id=?", (chat_id,)
        )
        conv_id = row["conversation_id"] if row else None
    if conv_id is None:
        conv_id = db.execute(
            "INSERT INTO conversations (title, model, created_at, updated_at)"
            " VALUES (?,?,?,?)",
            (text[:60], _model(), _now(), _now()),
        )
        db.execute(
            "INSERT INTO telegram_sessions (chat_id, conversation_id, created_at)"
            " VALUES (?,?,?)",
            (chat_id, conv_id, _now()),
        )
        _sessions[chat_id] = conv_id
    db.execute(
        "INSERT INTO messages (conversation_id, role, content, created_at)"
        " VALUES (?,?,?,?)",
        (conv_id, "user", text, _now()),
    )
    db.execute("UPDATE conversations SET updated_at=? WHERE id=?", (_now(), conv_id))

    memories = db.query_all("SELECT content FROM memories ORDER BY id DESC LIMIT 20")
    conv = db.query_one("SELECT summary FROM conversations WHERE id=?", (conv_id,))
    history = db.query_all(
        "SELECT role, content FROM messages WHERE conversation_id=? AND role IN"
        " ('user','assistant') ORDER BY id",
        (conv_id,),
    )
    msgs = [{"role": h["role"], "content": h["content"]} for h in history][-20:]
    parts = []
    if memories:
        parts.append("Informations memorisees:\n" + "\n".join(f"- {m['content']}" for m in memories))
    if conv and conv["summary"]:
        parts.append(f"Resume des echanges precedents:\n{conv['summary']}")
    try:
        from backend.core.style import style_block

        bloc = style_block()
        if bloc:
            parts.append(bloc)
    except Exception:
        pass
    parts.append("Sois direct et concis, reponds en francais.")
    if parts:
        msgs.insert(0, {"role": "system", "content": "\n\n".join(parts)})

    try:
        reply_parts = []
        async for event in backend.chat_stream(_model(), msgs, {"temperature": 0.3, "num_ctx": 4096, "think": False}):
            if event.type == "token":
                reply_parts.append(event.content)
            elif event.type == "error":
                raise RuntimeError(event.content)
        answer = "".join(reply_parts).strip()
    except Exception as exc:
        await _send(bot, chat_id, f"Erreur: {exc}")
        return
    db.execute(
        "INSERT INTO messages (conversation_id, role, content, model, created_at)"
        " VALUES (?,?,?,?,?)",
        (conv_id, "assistant", answer, _model(), _now()),
    )
    db.execute("UPDATE conversations SET updated_at=? WHERE id=?", (_now(), conv_id))
    await _send(bot, chat_id, answer or "Je n'ai rien a repondre.")


async def _poll_loop() -> None:
    global _last_error, _sessions
    _sessions = {
        r["chat_id"]: r["conversation_id"]
        for r in get_db().query_all("SELECT chat_id, conversation_id FROM telegram_sessions")
    }
    offset = 0
    while True:
        try:
            token = _token()
            if not token:
                await asyncio.sleep(5)
                continue
            if not _enabled():
                await asyncio.sleep(5)
                continue
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.get(
                    f"https://api.telegram.org/bot{token}/getUpdates",
                    params={"timeout": 50, "offset": offset, "allowed_updates": json.dumps(["message"])},
                )
                payload = resp.json()
                if not payload.get("ok"):
                    _last_error = payload.get("description", "api error")
                    await asyncio.sleep(10)
                    continue
                updates = payload.get("result", [])
                for upd in updates:
                    if upd["update_id"] >= offset:
                        offset = upd["update_id"] + 1
                    msg = upd.get("message") or {}
                    chat = msg.get("chat") or {}
                    cid = chat.get("id")
                    ttext = msg.get("text", "").strip()
                    if cid is not None and ttext:
                        asyncio.create_task(_handle_message(token, cid, ttext))
                _last_error = ""
        except asyncio.CancelledError:
            break
        except Exception as exc:
            _last_error = str(exc)
            await asyncio.sleep(10)


async def start_poller() -> None:
    global _poller
    if _poller is None or _poller.done():
        _poller = asyncio.create_task(_poll_loop())
    return _poller


async def stop_poller() -> None:
    global _poller
    if _poller:
        _poller.cancel()
        try:
            await _poller
        except asyncio.CancelledError:
            pass
        _poller = None


class TelegramConfig(BaseModel):
    token: str = ""
    enabled: bool | None = None
    model: str = ""


@router.get("/status")
async def telegram_status() -> dict:
    return {
        "configured": bool(_token()),
        "enabled": _enabled(),
        "model": _model(),
        "poller_running": bool(_poller and not _poller.done()),
        "last_error": _last_error,
        "sessions": len(_sessions),
    }


@router.put("/config")
async def telegram_configure(req: TelegramConfig) -> dict:
    db = get_db()
    pairs = []
    if req.token:
        pairs.append((_KEY_TOKEN, req.token))
    if req.enabled is not None:
        pairs.append((_KEY_ENABLED, "1" if req.enabled else "0"))
    if req.model:
        pairs.append((_KEY_MODEL, req.model))
    for key, value in pairs:
        if db.query_one("SELECT key FROM app_settings WHERE key=?", (key,)):
            db.execute("UPDATE app_settings SET value=? WHERE key=?", (value, key))
        else:
            db.execute("INSERT INTO app_settings (key, value) VALUES (?,?)", (key, value))
    return {"ok": True}


@router.post("/check")
async def telegram_check() -> dict:
    token = _token()
    if not token:
        return {"ok": False, "error": "token absent"}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(f"https://api.telegram.org/bot{token}/getMe")
            data = resp.json()
        if data.get("ok"):
            return {"ok": True, "bot": (data.get("result") or {}).get("username")}
        return {"ok": False, "error": data.get("description", "erreur")}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


class TelegramSendRequest(BaseModel):
    text: str = ""
    images: list[str] = Field(default_factory=list)
    chat_id: int | None = None


@router.post("/send")
async def telegram_send(req: TelegramSendRequest) -> dict:
    """Envoi manuel depuis l'interface web : texte + images generees."""
    from pathlib import Path

    from backend.core.security import verifier_acces

    token = _token()
    if not token:
        return {"ok": False, "error": "token absent"}
    if not _enabled():
        return {"ok": False, "error": "telegram desactive"}
    if not req.text.strip() and not req.images:
        return {"ok": False, "error": "rien a envoyer"}
    chat_id = req.chat_id
    if chat_id is None:
        row = get_db().query_one(
            "SELECT chat_id FROM telegram_sessions ORDER BY created_at DESC LIMIT 1"
        )
        chat_id = row["chat_id"] if row else None
    if chat_id is None:
        return {"ok": False, "error": "aucune session telegram (envoyez /start au bot)"}
    envoyes = 0
    images_ok = 0
    async with httpx.AsyncClient(timeout=60) as client:
        if req.text.strip():
            try:
                await _send(token, chat_id, req.text)
                envoyes += 1
            except Exception as exc:
                return {"ok": False, "error": f"sendMessage: {exc}"}
        for chemin in req.images[:10]:
            p = Path(chemin).resolve()
            if verifier_acces(str(p)) or not p.is_file():
                continue
            if p.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
                continue
            if p.stat().st_size > 50_000_000:
                continue
            with open(p, "rb") as fic:
                if p.stat().st_size <= 10_000_000:
                    r = await client.post(
                        f"https://api.telegram.org/bot{token}/sendPhoto",
                        files={"photo": (p.name, fic, "image/png")},
                        data={"chat_id": str(chat_id), "caption": "Envoye depuis Neo"},
                    )
                else:
                    r = await client.post(
                        f"https://api.telegram.org/bot{token}/sendDocument",
                        files={"document": (p.name, fic, "image/png")},
                        data={"chat_id": str(chat_id)},
                    )
            if r.status_code < 400:
                images_ok += 1
    if envoyes == 0 and images_ok == 0:
        return {"ok": False, "error": "aucune image valide"}
    return {"ok": True, "sent": envoyes, "images": images_ok}