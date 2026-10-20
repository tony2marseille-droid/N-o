# -*- coding: utf-8 -*-
"""Apprentissage par categorie : recherche web, synthese, memorisation RAG.

Usage : python -u apprentissage.py <categorie>
Le cerveau cherche le sujet sur le web, synthetise, puis stocke le resultat
dans la base documentaire (categorie forcee) : il devient interrogeable.
"""
import json
import sqlite3
import struct
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, ".")

SUJETS = {
    "programmation": (
        "Apprendre la programmation en Python : bases, structures essentielles, "
        "bonnes pratiques et erreurs courantes des debutants",
        "la programmation",
    ),
    "ia": (
        "Comprendre l intelligence artificielle simplement : ce que c est, comment "
        "fonctionnent les modeles de langage, usages quotidiens et limites",
        "l intelligence artificielle",
    ),
    "impression3d": (
        "L impression 3D pour debutants : materiels, materiaux, logiciels, conseils "
        "pratiques et erreurs a eviter",
        "l impression 3D",
    ),
    "bricolage": (
        "Le bricolage essentiel : outils de base, techniques courantes, reparations "
        "frequentes et conseils de securite",
        "le bricolage",
    ),
    "cuisine": (
        "La cuisine pour tous : techniques de base, recettes essentielles, erreurs "
        "courantes et astuces de conservation",
        "la cuisine",
    ),
    "sante": (
        "Les bases de la sante au quotidien : hygiene de vie, nutrition, sommeil, "
        "activite physique",
        "la sante",
    ),
    "finances": (
        "Gerer son argent au quotidien : budget, epargne, credits, pieges courants "
        "a eviter",
        "la gestion de l argent",
    ),
    "loisirs": (
        "Idees de loisirs et activites personnelles : lecture, jeux, sport, culture, "
        "conseils pour commencer",
        "les loisirs",
    ),
    "science": (
        "La science expliquee simplement : grands principes, decouvertes majeures, "
        "phenomenes du quotidien",
        "les sciences",
    ),
    "autre": (
        "Culture generale utile au quotidien : savoirs pratiques et essentiels",
        "la culture generale",
    ),
}

OLLAMA = "http://127.0.0.1:11434"
EMBED = "nomic-embed-text:latest"


def log(msg: str) -> None:
    print(f"[apprentissage] {msg}", flush=True)


def chunker(texte: str, fen: int = 800, rec: int = 100) -> list[str]:
    morceaux = []
    debut = 0
    n = len(texte)
    while debut < n:
        fin = min(debut + fen, n)
        part = texte[debut:fin].strip()
        if part:
            morceaux.append(part)
        if fin >= n:
            break
        debut = fin - rec
    return morceaux


def embed(textes: list[str]) -> list[list[float]]:
    body = json.dumps({"model": EMBED, "input": textes}).encode("utf-8")
    req = urllib.request.Request(
        f"{OLLAMA}/api/embed", data=body, headers={"Content-Type": "application/json"}
    )
    rep = json.loads(urllib.request.urlopen(req, timeout=180).read().decode("utf-8"))
    return rep.get("embeddings") or []


def main(cat: str) -> int:
    if cat not in SUJETS:
        log(f"categorie inconnue : {cat}")
        return 1
    sujet, titre = SUJETS[cat]
    log(f"categorie : {cat}")
    log(f"recherche en cours sur {titre}...")

    from backend.tools.t_web import tool_deep_research

    try:
        synthese = tool_deep_research(sujet, 2)
    except Exception as exc:
        log(f"erreur de recherche : {exc}")
        return 1
    if not synthese or len(synthese) < 200:
        log("recherche insuffisante (reseau indisponible ?)")
        return 1
    log(f"synthese obtenue : {len(synthese)} caracteres")

    date_fr = time.strftime("%d/%m/%Y")
    entete = (
        f"Connaissances que Neo a apprises sur {titre}\n"
        f"Session d'apprentissage du {date_fr} (recherche web + synthese)\n\n"
    )
    contenu = entete + synthese

    racine = Path("data/documents")
    racine.mkdir(parents=True, exist_ok=True)
    nom = f"apprentissage_{cat}_{time.strftime('%Y%m%d_%H%M%S')}.txt"
    chemin = racine / nom
    chemin.write_text(contenu, encoding="utf-8")
    log(f"document cree : {chemin}")

    morceaux = chunker(contenu)
    log(f"memorisation : {len(morceaux)} extraits")
    vecteurs: list[list[float]] = []
    for i in range(0, len(morceaux), 16):
        vecteurs.extend(embed(morceaux[i : i + 16]))
        log(f"memorisation : {min(i + 16, len(morceaux))}/{len(morceaux)}")

    con = sqlite3.connect("data/localai.db")
    try:
        cur = con.execute(
            "INSERT INTO documents (filename, size_bytes, chunk_count, created_at, "
            "category, description) VALUES (?,?,?,?,?,?)",
            (
                nom,
                chemin.stat().st_size,
                len(morceaux),
                time.strftime("%Y-%m-%dT%H:%M:%S"),
                cat,
                f"Ce que Neo a appris sur {titre} (lecon du {date_fr})",
            ),
        )
        doc_id = cur.lastrowid
        for pos, (morceau, vect) in enumerate(zip(morceaux, vecteurs)):
            blob = b"".join(struct.pack("<f", x) for x in vect)
            con.execute(
                "INSERT INTO document_chunks (document_id, position, content, embedding) "
                "VALUES (?,?,?,?)",
                (doc_id, pos, morceau, blob),
            )
        con.commit()
    finally:
        con.close()
    log(f"TERMINE : {len(morceaux)} extraits memorises dans la categorie {cat}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else ""))
