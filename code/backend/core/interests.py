"""Themes d'interet declares a l'installation : pilotent la priorite de
distillation (tous les sujets restent appris, ceux-ci en premier).
"""

import json

from backend.core.database import get_db

THEMES = [
    {"key": "impression3d", "label": "Impression 3D & CAO", "mots": ["stl", "gcode", "impression 3d", "openscad", "mesh", "filament", "extruder", "plateau", "slicer", "fusion", "cad", "3d"]},
    {"key": "programmation", "label": "Programmation", "mots": ["python", "code", "fonction", "bug", "api", "javascript", "script", "compile", "erreur de syntaxe", "git"]},
    {"key": "cuisine", "label": "Cuisine", "mots": ["recette", "cuisine", "four", "ingredients", "cuisiner", "plat", "gateau", "temperature de cuisson"]},
    {"key": "musique", "label": "Musique", "mots": ["musique", "accord", "guitare", "piano", "chanson", "album", "rythme", "paroles"]},
    {"key": "bricolage", "label": "Bricolage & maker", "mots": ["percer", "vis", "bois", "outil", "reparer", "monter", "plan de montage", "arduino", "raspberry"]},
    {"key": "mecanique", "label": "Mecanique auto/moto", "mots": ["moteur", "vidange", "frein", "courroie", "voiture", "moto", "pneu", "batterie", "distribition", "carburateur"]},
    {"key": "jardinage", "label": "Jardinage & plantes", "mots": ["plante", "graine", "potager", "arrosage", "taille", "engrais", "bouture", "terreau"]},
    {"key": "sante", "label": "Sante & sport", "mots": ["sante", "muscle", "entrainement", "nutrition", "sommeil", "etirement", "cardio", "medicament"]},
    {"key": "histoire", "label": "Histoire & culture", "mots": ["histoire", "guerre", "empire", "siecle", "civilisation", "revolution", "musee", "art"]},
    {"key": "sciences", "label": "Sciences", "mots": ["physique", "chimie", "biologie", "astronomie", "experience", "theorie", "quantique", "energie"]},
    {"key": "finance", "label": "Finance & administratif", "mots": ["budget", "impot", "epargne", "facture", "contrat", "assurance", "banque", "investir"]},
    {"key": "jeux", "label": "Jeux video & loisirs", "mots": ["jeux", "gameplay", "console", "strategie", "personnage", "niveau", "mod"]},
    {"key": "langues", "label": "Langues & traduction", "mots": ["traduire", "anglais", "espagnol", "grammaire", "vocabulaire", "conjugaison", "accent"]},
    {"key": "ia", "label": "IA & technologies", "mots": ["intelligence artificielle", "llm", "modele", "reseau de neurones", "prompt", "ollama", "gpu", "fine-tun"]},
]

SETUP_KEY = "interest_themes"


def catalogue() -> list[dict]:
    return [{"key": t["key"], "label": t["label"]} for t in THEMES]


def selection() -> list[str]:
    row = get_db().query_one("SELECT value FROM app_settings WHERE key=?", (SETUP_KEY,))
    if not row:
        return []
    try:
        vals = json.loads(row["value"])
        return [v for v in vals if isinstance(v, str)]
    except Exception:
        return []


def definir(cles: list[str]) -> list[str]:
    valides = [k for k in cles if k in {t["key"] for t in THEMES}]
    db = get_db()
    db.execute(
        f"INSERT INTO app_settings (key, value) VALUES ('{SETUP_KEY}', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (json.dumps(valides, ensure_ascii=False),),
    )
    return valides


def score_texte(texte: str) -> int:
    """Nombre de mots-cles des themes choisis presents dans le texte."""
    bas = (texte or "").lower()
    sel = {t["key"]: set(t["mots"]) for t in THEMES if t["key"] in selection()}
    total = 0
    for mots in sel.values():
        total += sum(1 for m in mots if m in bas)
    return total


def trier_par_priorite(lignes: list[dict], champ: str = "content") -> list[dict]:
    """Trie : d'abord les lignes touchant les themes d'interet, puis les autres."""
    def cle(row):
        return -score_texte(str(row.get(champ) or ""))
    return sorted(lignes, key=cle)
