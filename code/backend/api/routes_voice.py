import asyncio
import json
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse, StreamingResponse

from backend.api.routes_settings import get_setting
from backend.core.config import settings
from backend.core.database import get_db
from backend.core.knowledge import lookup as knowledge_lookup, remember as knowledge_remember
from backend.core.vector_store import Embedder
from backend.inference import get_active_backend
from backend.inference.ollama import OllamaError
from backend.tts.engine import (
    TTSEngineError,
    cleanup_cache,
    new_wav_path,
    synthesize_to_wav,
)

router = APIRouter(prefix="/api/voice", tags=["voice"])

MAX_HISTORY = 24
_VOICE_DIR = None


def _voice_dir() -> Path:
    global _VOICE_DIR
    if _VOICE_DIR is None:
        _VOICE_DIR = settings.data_dir / "voice"
        _VOICE_DIR.mkdir(parents=True, exist_ok=True)
    return _VOICE_DIR


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


@router.get("/status")
async def status() -> dict:
    from backend.core import stt

    return {
        "stt_available": stt.stt_available(),
        "stt_model": get_setting("stt_model", "small"),
        "tts_available": True,
        "voice_dir": str(_voice_dir()),
    }


@router.post("/transcribe")
async def transcribe_audio(file: UploadFile = File(...)) -> dict:
    from backend.core import stt

    ext = Path(file.filename or "audio.webm").suffix.lower() or ".webm"
    tmp = _voice_dir() / f"in_{uuid.uuid4().hex}{ext}"
    try:
        tmp.write_bytes(await file.read())
        if not stt.stt_available():
            raise HTTPException(status_code=503, detail="STT indisponible (faster-whisper non installe)")
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(stt.transcribe, tmp), timeout=120
            )
        except asyncio.TimeoutError:
            raise HTTPException(status_code=504, detail="Transcription trop lente") from None
        return result
    finally:
        tmp.unlink(missing_ok=True)


def _build_system_prompt_light(memories: list[dict], summary: str) -> str:
    parts: list[str] = []
    if memories:
        lines = "\n".join(f"- {m['content']}" for m in memories)
        parts.append("Informations memorisees a propos de l'utilisateur:\n" + lines)
    if summary:
        parts.append(f"Resume des echanges precedents:\n{summary}")
    try:
        from backend.core.style import style_block

        bloc = style_block()
        if bloc:
            parts.append(bloc)
    except Exception:
        pass
    parts.append(
        "Style : reponds en francais, de facon concise et naturelle pour de la "
        "transcription vocale (phrases courtes, pas de markdown, pas de liste "
        "technique). Le ton doit etre chaleureux et direct."
    )
    return "\n\n".join(p for p in parts if p)


async def _voice_chat_stream(
    transcript: str,
    conversation_id: int | None,
    model: str,
    temperature: float,
    tts_output: bool,
):
    db = get_db()
    backend = get_active_backend()

    if conversation_id is None:
        title = transcript.strip()[:60] or "Echange vocal"
        conversation_id = db.execute(
            "INSERT INTO conversations (title, model, created_at, updated_at) VALUES (?,?,?,?)",
            (title, model, _now(), _now()),
        )
    else:
        conv = db.query_one("SELECT id, summary FROM conversations WHERE id=?", (conversation_id,))
        if conv is None:
            yield _sse({"type": "error", "error": "Conversation introuvable"})
            return
        db.execute(
            "UPDATE conversations SET updated_at=?, model=? WHERE id=?",
            (_now(), model, conversation_id),
        )

    db.execute(
        "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?,?,?,?)",
        (conversation_id, "user", transcript, _now()),
    )

    memory_hit = None
    if get_setting("auto_learn", "1") != "0":
        try:
            memory_hit = await knowledge_lookup(Embedder(backend), transcript)
        except Exception:
            memory_hit = None

    yield _sse(
        {
            "type": "meta",
            "conversation_id": conversation_id,
            "model": "Memoire vocale" if memory_hit else model,
            "route_reason": f"connaissance apprise (score {memory_hit['score']})" if memory_hit else "transcription vocale",
            "learned": bool(memory_hit),
            "transcript": transcript,
        }
    )

    if memory_hit:
        answer = memory_hit["answer"]
        step = max(1, len(answer) // 4)
        for i in range(0, len(answer), step):
            yield _sse({"type": "token", "content": answer[i : i + step]})
        aid = db.execute(
            "INSERT INTO messages (conversation_id, role, content, model, created_at)"
            " VALUES (?,?,?,?,?)",
            (conversation_id, "assistant", answer, "memoire", _now()),
        )
        if tts_output:
            yield _sse({"type": "tts", "conversation_id": conversation_id, "assistant_message_id": aid})
        yield _sse({"type": "done", "conversation_id": conversation_id, "assistant_message_id": aid, "learned": True, "duration_ms": 5})
        return

    rows = db.query_all(
        "SELECT role, content FROM messages WHERE conversation_id=? AND role IN"
        " ('user','assistant') ORDER BY id",
        (conversation_id,),
    )
    history = [{"role": r["role"], "content": r["content"]} for r in rows][-MAX_HISTORY:]
    memories = db.query_all("SELECT content FROM memories ORDER BY id DESC LIMIT 30")
    conv_row = db.query_one("SELECT summary FROM conversations WHERE id=?", (conversation_id,))
    summary = conv_row["summary"] if conv_row else ""

    system = _build_system_prompt_light(memories, summary)
    if system:
        history.insert(0, {"role": "system", "content": system})

    full_parts: list[str] = []
    stats_payload: dict = {}
    started = datetime.now()
    try:
        async for event in backend.chat_stream(
            model,
            history,
            {"temperature": temperature, "num_ctx": 4096, "think": False},
        ):
            if event.type == "token":
                full_parts.append(event.content)
                yield _sse({"type": "token", "content": event.content})
            elif event.type == "done":
                s = event.stats
                stats_payload = {
                    "prompt_tokens": s.prompt_tokens,
                    "completion_tokens": s.completion_tokens,
                    "duration_ms": s.total_duration_ms,
                    "tokens_per_second": s.tokens_per_second,
                }
            elif event.type == "error":
                yield _sse({"type": "error", "error": event.content})
                return
    except OllamaError as exc:
        yield _sse({"type": "error", "error": str(exc)})
        return
    except Exception as exc:
        yield _sse({"type": "error", "error": f"erreur interne: {exc!r}"})
        return

    content = "".join(full_parts)
    duration_ms = int((datetime.now() - started).total_seconds() * 1000)
    aid = db.execute(
        "INSERT INTO messages (conversation_id, role, content, model,"
        " prompt_tokens, completion_tokens, duration_ms, created_at)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (
            conversation_id,
            "assistant",
            content,
            model,
            stats_payload.get("prompt_tokens", 0),
            stats_payload.get("completion_tokens", 0),
            duration_ms,
            _now(),
        ),
    )
    db.execute("UPDATE conversations SET updated_at=? WHERE id=?", (_now(), conversation_id))
    if tts_output and content:
        try:
            wav = new_wav_path(settings.tts_cache_dir)
            synthesize_to_wav(content[:900], wav)
            cleanup_cache(settings.tts_cache_dir)
            yield _sse({"type": "tts", "file": wav.name})
        except TTSEngineError:
            pass
    yield _sse(
        {
            "type": "done",
            "conversation_id": conversation_id,
            "assistant_message_id": aid,
            "duration_ms": duration_ms,
            **{k: v for k, v in stats_payload.items() if k != "duration_ms"},
        }
    )
    if get_setting("auto_learn", "1") != "0":
        asyncio.create_task(knowledge_remember(Embedder(backend), transcript, content))


@router.post("/chat")
async def voice_chat(
    file: UploadFile = File(...),
    conversation_id: int | None = Form(None),
    model: str = Form("auto"),
    temperature: float = Form(0.3),
    tts_output: bool = Form(True),
) -> StreamingResponse:
    from backend.core import stt

    if not stt.stt_available():
        raise HTTPException(status_code=503, detail="STT indisponible (faster-whisper non installe)")

    ext = Path(file.filename or "audio.webm").suffix.lower() or ".webm"
    tmp = _voice_dir() / f"in_{uuid.uuid4().hex}{ext}"
    try:
        tmp.write_bytes(await file.read())
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(stt.transcribe, tmp), timeout=120
            )
        except asyncio.TimeoutError:
            raise HTTPException(status_code=504, detail="Transcription trop lente") from None
    finally:
        tmp.unlink(missing_ok=True)

    transcript = (result.get("text") or "").strip()
    if not transcript:
        return StreamingResponse(
            iter([_sse({"type": "error", "error": "Aucune parole detectee"})]),
            media_type="text/event-stream",
        )

    if model == "auto":
        model = get_setting("voice_model", "gemma3:4b") or "gemma3:4b"

    concrete_id = conversation_id
    async def generator():
        async for chunk in _voice_chat_stream(
            transcript, concrete_id, model, temperature, tts_output
        ):
            yield chunk

    return StreamingResponse(generator(), media_type="text/event-stream")


@router.get("/wav/{filename}")
async def voice_wav(filename: str) -> FileResponse:
    path = settings.tts_cache_dir / filename
    if path.suffix.lower() != ".wav" or not path.exists():
        raise HTTPException(status_code=404, detail="Fichier introuvable")
    return FileResponse(path, media_type="audio/wav")