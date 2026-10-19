import threading
import uuid
from pathlib import Path

import pyttsx3


class TTSEngineError(Exception):
    pass


_FEMININ = ("zira", "hortense", "julie", "virginie", "hazel", "susan", "heera",
            "eva", "catherine", "amelie", "amélie", "audrey", "aurelie", "aurélie",
            "chantal", "charlotte", "female", "femme")


def _genre(nom: str, vid: str) -> str:
    bas = f"{nom} {vid}".lower()
    return "F" if any(m in bas for m in _FEMININ) else "M"


def list_voices() -> list[dict]:
    engine = pyttsx3.init()
    try:
        voices = []
        for v in engine.getProperty("voices"):
            langs = []
            for attr in ("languages", "language"):
                raw = getattr(v, attr, None)
                if raw:
                    langs = list(raw) if isinstance(raw, (list, tuple)) else [str(raw)]
                    break
            voices.append(
                {
                    "id": v.id,
                    "name": v.name,
                    "languages": langs,
                    "gender": _genre(v.name, v.id),
                }
            )
        # voix feminines d'abord : la selection par defaut les privilegie
        voices.sort(key=lambda x: (x["gender"] != "F", x["name"]))
        return voices
    finally:
        try:
            engine.stop()
        except Exception:
            pass


def voix_feminine_fr() -> str:
    """La meilleure voix feminine disponible pour le francais."""
    candidates = [
        v for v in list_voices()
        if v["gender"] == "F" and any("fr" in l.lower() for l in v["languages"])
    ]
    if not candidates:
        candidates = [v for v in list_voices() if v["gender"] == "F"]
    return candidates[0]["id"] if candidates else ""


def synthesize_to_wav(text: str, out_path: Path, voice_id: str = "", rate: int = 0) -> Path:
    text = text.strip()
    if not text:
        raise TTSEngineError("Texte vide")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    result: dict = {"path": None, "error": None}

    def work() -> None:
        try:
            engine = pyttsx3.init()
            if rate > 0:
                engine.setProperty("rate", rate)
            if voice_id:
                for v in engine.getProperty("voices"):
                    if v.id == voice_id:
                        engine.setProperty("voice", voice_id)
                        break
            engine.save_to_file(text, str(out_path))
            engine.runAndWait()
            engine.stop()
            if not out_path.exists() or out_path.stat().st_size == 0:
                result["error"] = "Synthèse sans sortie audio"
            else:
                result["path"] = out_path
        except Exception as exc:
            result["error"] = str(exc)

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    thread.join(timeout=60)
    if thread.is_alive():
        raise TTSEngineError("Délai de synthèse dépassé")
    if result["error"]:
        raise TTSEngineError(result["error"])
    return Path(result["path"])


def cleanup_cache(cache_dir: Path, keep: int = 40) -> None:
    files = sorted(cache_dir.glob("*.wav"), key=lambda p: p.stat().st_mtime)
    for old in files[:-keep]:
        old.unlink(missing_ok=True)


def new_wav_path(cache_dir: Path) -> Path:
    return cache_dir / f"tts_{uuid.uuid4().hex}.wav"
