import asyncio
import glob
import json
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from backend.core.config import settings
from backend.inference import get_active_backend
from backend.inference.ollama import OllamaError
from backend.tts.engine import (
    TTSEngineError,
    cleanup_cache,
    new_wav_path,
    synthesize_to_wav,
)

router = APIRouter(prefix="/api/studio", tags=["studio"])

# Studio Contenu Local : redaction + voix off + montage (ffmpeg).
# 100% local / gratuit - equivalents de copy.ai/Jasper (redaction LocalAI),
# ElevenLabs/Murf (TTS LocalAI), Invideo/Pictory (montage ffmpeg).

_STUDIO_DIR = None


def _dir() -> Path:
    global _STUDIO_DIR
    if _STUDIO_DIR is None:
        _STUDIO_DIR = settings.data_dir / "studio"
        _STUDIO_DIR.mkdir(parents=True, exist_ok=True)
    return _STUDIO_DIR


def _ffmpeg() -> Optional[str]:
    p = shutil.which("ffmpeg")
    if p:
        return p
    # Le serveur demarre via une tache planifiee (PATH restreint) : on cherche
    # aussi dans le depot WinGet, meme quand le PATH systeme n'est pas chargé.
    roots = [
        Path.home() / "AppData/Local/Microsoft/WinGet/Packages",
        Path("C:/Users/Tony/AppData/Local/Microsoft/WinGet/Packages"),
    ]
    for root in roots:
        if not root.exists():
            continue
        for exe in root.glob("Gyan.FFmpeg*/ffmpeg-*/bin/ffmpeg.exe"):
            return str(exe)
    return None


# ---------------------------------------------------------------------------
# Corps : generation locale via le backend (LocalAI/Ollama)
# ---------------------------------------------------------------------------
async def _gen(prompt: str, system: str = "", model: str = "gemma3:4b",
               max_tokens: int = 1024, temperature: float = 0.6) -> str:
    backend = get_active_backend()
    history = []
    if system:
        history.append({"role": "system", "content": system})
    history.append({"role": "user", "content": prompt})
    full: list[str] = []
    try:
        async for event in backend.chat_stream(
            model,
            history,
            {"temperature": temperature, "num_ctx": 4096,
             "max_tokens": max_tokens, "think": False},
        ):
            if event.type == "token":
                full.append(event.content)
            elif event.type == "error":
                raise RuntimeError(event.content)
    except OllamaError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"erreur: {exc!r}") from exc
    return "".join(full).strip()


# ---------------------------------------------------------------------------
# Redaction (copie de copy.ai / Jasper / nichesss)
# ---------------------------------------------------------------------------
class WriteRequest(BaseModel):
    sujet: str
    format: str = Field("article", description="article|post|script|email|resume")
    audience: str = ""
    ton: str = ""
    langue: str = "fr"
    model: str = "gemma3:4b"
    max_tokens: int = 1024


@router.post("/write")
async def studio_write(req: WriteRequest) -> dict:
    guide_format = {
        "article": "un article strucure avec titre, sous-titres et paragraphes",
        "post": "un texte court accrocheur adapte aux reseaux sociaux",
        "script": "un script video avec voix off et indications de scenes",
        "email": "un email professionnel avec objet et corps",
        "resume": "un resume clair et concis",
    }.get(req.format, "un contenu bien structure")
    system = (
        "Tu es un redacteur de contenu professionnel francophone. "
        f"Ecris en {req.langue}."
    )
    extras = []
    if req.audience:
        extras.append(f"Public cible : {req.audience}")
    if req.ton:
        extras.append(f"Ton : {req.ton}")
    guide = "\n".join(extras) if extras else ""
    prompt = (
        f"Redige {guide_format} sur le sujet suivant : {req.sujet}.\n{guide}"
    ).strip()
    texte = await _gen(prompt, system=system, model=req.model,
                       max_tokens=req.max_tokens)
    return {"ok": True, "format": req.format, "texte": texte}


# ---------------------------------------------------------------------------
# Voix off (ElevenLabs / Murf locaux)
# ---------------------------------------------------------------------------
class VoiceOverRequest(BaseModel):
    texte: str
    voice: str = ""
    rate: int = 0


@router.post("/voiceover")
async def studio_voiceover(req: VoiceOverRequest) -> FileResponse:
    wav = new_wav_path(settings.tts_cache_dir)
    try:
        path = synthesize_to_wav(req.texte, wav, voice_id=req.voice, rate=req.rate)
        cleanup_cache(settings.tts_cache_dir)
    except TTSEngineError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return FileResponse(path, media_type="audio/wav", filename=path.name)


@router.get("/voices")
async def studio_voices() -> dict:
    from backend.tts.engine import list_voices

    try:
        return {"voices": list_voices()}
    except Exception as exc:
        return {"voices": [], "detail": str(exc)}


# ---------------------------------------------------------------------------
# Sous-titres SRT (titre + voix off -> timing)
# ---------------------------------------------------------------------------
class SubsRequest(BaseModel):
    lignes: list[str] = Field(..., description="Phrases a sous-titrer")
    vitesse: float = Field(14.0, description="caracteres/seconde")


@router.post("/subs")
async def studio_subs(req: SubsRequest) -> FileResponse:
    out = _dir() / f"subs_{uuid.uuid4().hex}.srt"
    blocks = []
    t = 0.0
    for i, ligne in enumerate(req.lignes, start=1):
        duree = max(1.0, len(ligne) / req.vitesse)
        start_m = int(t // 60)
        start_s = int(t % 60)
        end = t + duree
        end_m = int(end // 60)
        end_s = int(end % 60)
        blocks.append(
            f"{i}\n{start_m:02d}:{start_s:02d},000 --> {end_m:02d}:{end_s:02d},000\n{ligne}\n"
        )
        t = end
    out.write_text("\n".join(blocks), encoding="utf-8")
    return FileResponse(out, media_type="application/x-subrip", filename=out.name)


# ---------------------------------------------------------------------------
# Montage video (Invideo / Pictory locaux) : diaporama + voix off + sous-titres
# ---------------------------------------------------------------------------
class VideoRequest(BaseModel):
    ligne_titre: str = ""
    lignes: list[str] = Field(..., description="Phrases successives (une par scene)")
    extraits: list[str] = Field([], description="Chemins d'images (opt, une par scene)")
    fps: int = 24
    taille: str = Field("1280x720")
    resize: str = Field("scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2:color=black")
    keep_audio: bool = True


@router.post("/video")
async def studio_video(req: VideoRequest) -> FileResponse:
    ff = _ffmpeg()
    if not ff:
        raise HTTPException(status_code=503, detail="ffmpeg introuvable sur le serveur")

    if not req.lignes:
        raise HTTPException(status_code=400, detail="Aucune phrase fournie")
    work = _dir() / f"proj_{uuid.uuid4().hex}"
    work.mkdir(parents=True, exist_ok=True)

    try:
        # 1) Decouper chaque phrase en un wav (voix off par scene)
        wavs = []
        for idx, phrase in enumerate(req.lignes, start=1):
            wav = new_wav_path(settings.tts_cache_dir)
            try:
                path = synthesize_to_wav(phrase, wav)
            except TTSEngineError as exc:
                raise HTTPException(status_code=500, detail=str(exc)) from exc
            wavs.append(path)

        durations: list[float] = []
        for w in wavs:
            try:
                probe = subprocess.run(
                    [ff, "-i", str(w), "-hide_banner", "-loglevel", "error"],
                    capture_output=True, text=True,
                )
                meta = probe.stderr or ""
                ds = meta.split("Duration:")[1].split(",")[0].strip() if "Duration:" in meta else "0:00:00.00"
                h, m, s = ds.split(":")
                durations.append(int(h) * 3600 + int(m) * 60 + float(s) + 0.4)
            except Exception:
                durations.append(1.0)

        # 2) Police pour drawtext (eviter fontconfig) : copie dans work, chemin relatif
        font = work / "arial.ttf"
        try:
            import shutil as _sh

            _sh.copy(r"C:\Windows\Fonts\arial.ttf", font)
        except Exception:
            font = None

        def _dt(s: str) -> str:
            s = s.replace("\\", "\\\\").replace("%", "%%")
            s = s.replace("'", "\\'").replace(":", "\\:").replace(",", "\\,").replace(";", "\\;")
            return s

        # 3) Construire une liste de segments video (image/color + voix off + texte grave)
        segs = []
        imgs = list(req.extraits) + [""] * max(0, len(req.lignes) - len(req.extraits))
        for i, (img, wav, d, phrase) in enumerate(
            zip(imgs, wavs, durations, req.lignes), start=1
        ):
            seg = work / f"seg_{i:02d}.mp4"
            texte = req.ligne_titre if i == 1 and req.ligne_titre else phrase
            neg = _dt(texte[:60])
            if font:
                dt = (f"drawtext=fontfile=arial.ttf:text='{neg}'"
                      f":fontsize=44:fontcolor=white:borderw=3:bordercolor=black"
                      f":x=(w-text_w)/2:y=(h-text_h)/2")
            else:
                dt = None
            if img and Path(img).exists():
                vf = req.resize + (("," + dt) if dt else "")
                cmd = [ff, "-y", "-loop", "1", "-i", img, "-i", str(wav),
                       "-t", f"{d:.2f}", "-vf", vf, "-c:v", "libx264",
                       "-preset", "veryfast", "-tune", "stillimage", "-r", str(req.fps),
                       "-c:a", "aac", "-b:a", "128k", "-pix_fmt", "yuv420p",
                       "-shortest", str(seg)]
            else:
                vf = dt or "null"
                cmd = [ff, "-y", "-f", "lavfi", "-i", "color=c=0x222233:s=" + req.taille,
                       "-i", str(wav), "-t", f"{d:.2f}", "-vf", vf,
                       "-r", str(req.fps), "-c:v", "libx264", "-preset", "veryfast",
                       "-c:a", "aac", "-b:a", "128k", "-pix_fmt", "yuv420p",
                       "-shortest", str(seg)]
            subprocess.run(cmd, capture_output=True, cwd=str(work))
            segs.append(seg)

        # 4) Concatener
        concat = work / "concat.txt"
        concat.write_text(
            "\n".join(f"file 'seg_{i:02d}.mp4'" for i in range(1, len(segs) + 1)),
            encoding="utf-8",
        )
        merged = work / "merged.mp4"
        subprocess.run(
            [ff, "-y", "-f", "concat", "-safe", "0", "-i", "concat.txt",
             "-c", "copy", "merged.mp4"],
            capture_output=True, cwd=str(work),
        )

        final = _dir() / f"video_{uuid.uuid4().hex}.mp4"
        merged_dst = work / "final.mp4"
        subprocess.run(
            [ff, "-y", "-i", "merged.mp4", "-c", "copy", "final.mp4"],
            capture_output=True, cwd=str(work),
        )
        import shutil as _sh2

        _sh2.move(str(work / "final.mp4"), str(final))
        return FileResponse(final, media_type="video/mp4", filename=final.name)
    finally:
        import shutil as _sh

        _sh.rmtree(work, ignore_errors=True)
        cleanup_cache(settings.tts_cache_dir)


@router.get("/status")
async def studio_status() -> dict:
    return {
        "ffmpeg": bool(_ffmpeg()),
        "ffmpeg_path": _ffmpeg(),
        "tts": True,
        "modele_default": "gemma3:4b",
        "catalogue": ["redaction (write)", "voix off (voiceover)",
                      "sous-titres (subs)", "montage video (video)"],
    }
