# -*- coding: utf-8 -*-
"""Coach d'auto-amelioration : analyse les signaux faibles de la semaine
(feedback utilisateur, auto-evaluations, erreurs d'outils) et transforme les
problemes recurrents en REGLES D'AMELIORATION injectees dans le prompt systeme.

Lance : hebdomadaire (tache planifiee) ou manuellement via
POST /api/selfimprove/coach.
"""
import re
import time
from datetime import datetime, timedelta

import httpx

from backend.core.database import get_db

_MODELE_COACH = "qwen3:14b"
_OLLAMA = "http://127.0.0.1:11434"


def _init_tables(db) -> None:
    db.execute(
        "CREATE TABLE IF NOT EXISTS feedback (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "conversation_id INTEGER, message_id INTEGER, rating INTEGER, note TEXT, created_at TEXT)"
    )
    db.execute(
        "CREATE TABLE IF NOT EXISTS auto_evals (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "conversation_id INTEGER, score INTEGER, rationale TEXT, created_at TEXT)"
    )
    db.execute(
        "CREATE TABLE IF NOT EXISTS ameliorations (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "regle TEXT, source TEXT, active INTEGER DEFAULT 1, created_at TEXT)"
    )


def _depuis_7j() -> str:
    return (datetime.now() - timedelta(days=7)).isoformat(timespec="seconds")


def _analyser(db) -> dict:
    depuis = _depuis_7j()
    signaux: list[str] = []

    for ligne in db.query_all(
        "SELECT rating, note, conversation_id FROM feedback WHERE rating <= 3 AND created_at >= ? "
        "ORDER BY id DESC LIMIT 15", (depuis,)
    ):
        ctx = ""
        try:
            m = db.query_one(
                "SELECT content FROM messages WHERE conversation_id=? AND role='user' "
                "ORDER BY id DESC LIMIT 1", (ligne.get("conversation_id"),)
            )
            if m:
                ctx = str(m.get("content", ""))[:200]
        except Exception:
            pass
        signaux.append(
            f"[Note {ligne.get('rating')}/5] demande: « {ctx} » | remarque: {ligne.get('note', '')[:200]}"
        )

    for ligne in db.query_all(
        "SELECT score, rationale, conversation_id FROM auto_evals WHERE score <= 6 AND created_at >= ? "
        "ORDER BY id DESC LIMIT 15", (depuis,)
    ):
        signaux.append(
            f"[Auto-eval {ligne.get('score')}/10] justification: {ligne.get('rationale', '')[:250]}"
        )

    for sql in (
        "SELECT tool_name, result_preview FROM tool_traces WHERE "
        "(result_preview LIKE '%Erreur%' OR status='denied') AND created_at >= ? "
        "ORDER BY id DESC LIMIT 20",
        "SELECT tool_name, result_preview FROM tool_traces WHERE "
        "result_preview LIKE '%Erreur%' AND created_at >= ? ORDER BY id DESC LIMIT 20",
    ):
        try:
            lignes = db.query_all(sql, (depuis,))
            if lignes:
                break
        except Exception:
            continue
    for ligne in lignes:
        signaux.append(
            f"[Erreur outil {ligne.get('tool_name')}] {str(ligne.get('result_preview', ''))[:200]}"
        )
    return {"signaux": signaux}


def _regles_existantes(db) -> list[str]:
    return [
        str(l.get("regle", ""))
        for l in db.query_all("SELECT regle FROM ameliorations WHERE active=1")
    ]


def _dedup(regle: str, existantes: list[str]) -> bool:
    mots = set(re.findall(r"[a-zàâçéèêëîïôùûüÿ]{4,}", regle.lower()))
    for e in existantes:
        mots_e = set(re.findall(r"[a-zàâçéèêëîïôùûüÿ]{4,}", e.lower()))
        if mots and mots_e and len(mots & mots_e) / max(1, len(mots_e)) > 0.6:
            return True
    return False


def _demander_regles(signaux: list[str]) -> list[str]:
    if not signaux:
        return []
    bloc = "\n".join(f"- {s}" for s in signaux[:40])
    prompt = (
        "Tu es le coach d'un assistant IA local (LocalAI) qui tourne sur un PC. "
        "Voici les signaux faibles de la semaine (notes utilisateur basses, "
        "auto-evaluations faibles, erreurs d'outils) :\n\n"
        f"{bloc}\n\n"
        "Redige 1 a 5 REGLES D'AMELIORATION concretes et actionnables, en "
        "francais, une par ligne, chacune precedee de '- '. Chaque regle doit "
        "corriger un probleme recurrent observe ci-dessus (ex: « - quand une "
        "recherche web echoue, propose des synonymes avant d'abandonner »). "
        "Si aucun signal n'est exploitable, reponds exactement : RIEN."
    )
    try:
        r = httpx.post(
            f"{_OLLAMA}/api/chat",
            json={
                "model": _MODELE_COACH,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "think": False,
                "options": {"temperature": 0.3, "num_predict": 300},
            },
            timeout=180,
        )
        texte = r.json().get("message", {}).get("content", "") or ""
    except Exception as exc:
        return [f"Coach indisponible: {exc}"]
    if "RIEN" in texte.upper():
        return []
    regles = []
    for ligne in texte.splitlines():
        ligne = ligne.strip()
        m = re.match(r"^[-*•]\s+(.+)", ligne)
        if m and 10 <= len(m.group(1)) <= 300:
            regles.append(m.group(1))
    return regles[:5]


def lancer() -> dict:
    """Analyse la semaine et enregistre les nouvelles regles. Retourne un
    rapport JSON-serialisable."""
    db = get_db()
    _init_tables(db)
    analyse = _analyser(db)
    signaux = analyse["signaux"]
    regles = _demander_regles(signaux)
    existantes = _regles_existantes(db)
    nouvelles = []
    now = datetime.now().isoformat(timespec="seconds")
    for regle in regles:
        if regle.startswith("Coach indisponible"):
            return {"new_rules": 0, "error": regle, "analysed": len(signaux)}
        if _dedup(regle, existantes):
            continue
        db.execute(
            "INSERT INTO ameliorations (regle, source, active, created_at) VALUES (?,?,1,?)",
            (regle, "coach", now),
        )
        nouvelles.append(regle)
        existantes.append(regle)
    rapport = {
        "new_rules": len(nouvelles),
        "rules": nouvelles,
        "analysed_signals": len(signaux),
        "total_rules": len(existantes),
    }
    return rapport


if __name__ == "__main__":
    import json

    print(json.dumps(lancer(), ensure_ascii=False, indent=2))
