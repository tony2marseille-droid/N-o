import asyncio
import json
import re
from datetime import datetime, timedelta

from backend.core.database import get_db

KEY_PROFILE = "style_profile"
KEY_AUTO = "style_auto"
KEY_LAST = "style_last_refresh"

DEFAULT_PROFILE = {
    "formality": "courant",
    "verbosity": "equilibre",
    "emoji": "none",
    "tone": "direct",
    "typo": "correcte",
    "tech_level": "intermediaire",
    "langue": "fr",
    "description": "",
}


def _load_setting(key: str, default: str = "") -> str:
    row = get_db().query_one("SELECT value FROM app_settings WHERE key=?", (key,))
    return row["value"] if row else default


def _save_setting(key: str, value: str) -> None:
    db = get_db()
    if db.query_one("SELECT key FROM app_settings WHERE key=?", (key,)):
        db.execute("UPDATE app_settings SET value=? WHERE key=?", (value, key))
    else:
        db.execute("INSERT INTO app_settings (key, value) VALUES (?,?)", (key, value))


def style_enabled() -> bool:
    return _load_setting(KEY_AUTO, "1") == "1"


def get_style_profile() -> dict:
    raw = _load_setting(KEY_PROFILE, "")
    if not raw:
        return dict(DEFAULT_PROFILE)
    try:
        data = json.loads(raw)
        merged = dict(DEFAULT_PROFILE)
        merged.update({k: v for k, v in data.items() if k in DEFAULT_PROFILE})
        return merged
    except Exception:
        return dict(DEFAULT_PROFILE)


def save_style_profile(profile: dict) -> None:
    merged = dict(DEFAULT_PROFILE)
    merged.update({k: v for k, v in profile.items() if k in DEFAULT_PROFILE})
    _save_setting(KEY_PROFILE, json.dumps(merged, ensure_ascii=False))
    _save_setting(KEY_LAST, datetime.now().isoformat(timespec="seconds"))


def style_block() -> str:
    """Bloc injectable dans le prompt systeme si le style adaptatif est actif."""
    if not style_enabled():
        return ""
    p = get_style_profile()
    lignes = (
        f"- niveau de langue: {p['formality']} "
        f"(niveau technique: {p['tech_level']})",
        f"- longueur des reponses: {p['verbosity']}",
        f"- ton: {p['tone']}",
        f"- emoji/symboles: {p['emoji']}",
        f"- orthographe de l'utilisateur: {p['typo']}",
    )
    bloc = "\n".join(lignes)
    if p["description"]:
        bloc += f"\n- affinites de l'utilisateur : {p['description'][:400]}"
    return (
        "Style de communication adapte a l'utilisateur (tu te cales dessus "
        "naturellement, sans le mentionner) :\n" + bloc
    )


def _parse_json_report(raw: str) -> dict:
    m = re.search(r"\{[^{}]*\}", raw, re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}


async def analyze_style(backend, model: str = "gemma3:4b", echantillon: int = 40) -> dict:
    """Analyse les derniers messages de l'utilisateur et enregistre le profil."""
    rows = get_db().query_all(
        "SELECT content FROM messages WHERE role='user' AND content NOT LIKE"
        "    ' [image]%' ORDER BY id DESC LIMIT ?",
        (echantillon,),
    )
    textes = [r["content"][:500] for r in reversed(rows)]
    if not textes:
        return {}
    corpus = "\n".join(f"- {t}" for t in textes[-15:])
    prompt = (
        "A partir des messages utilisateur ci-dessous, produis UNIQUEMENT un objet JSON "
        "(sans texte avant/apres) avec ces champs : formality (familier/courant/soigne), "
        "verbosity (court/equilibre/eloquent), emoji (none/leger/abondant), "
        "tone (direct/amical/ironique/calme/professionnel), typo (correcte/relachee), "
        "tech_level (debutant/intermediaire/expert), langue (fr/en/autre), et "
        "description (10-30 mots, affinites, vocabulaire, centres d'interet).\n\n"
        f"Messages :\n{corpus}"
    )
    parts: list[str] = []
    try:
        async for event in backend.chat_stream(
            model, [{"role": "user", "content": prompt}], {"temperature": 0.2}
        ):
            if event.type == "token":
                parts.append(event.content)
            elif event.type == "error":
                return {}
    except Exception:
        return {}
    profil = _parse_json_report("".join(parts))
    if profil:
        save_style_profile(profil)
    return profil


async def maybe_refresh_style(backend) -> None:
    """Mise a jour periodique (1x/jour max) si le mode auto est actif."""
    if not style_enabled():
        return
    dernier = _load_setting(KEY_LAST, "")
    if dernier:
        try:
            dt = datetime.fromisoformat(dernier)
            if datetime.now() - dt < timedelta(hours=24):
                return
        except ValueError:
            pass
    try:
        await asyncio.wait_for(analyze_style(backend), timeout=180)
    except (asyncio.TimeoutError, Exception):
        pass