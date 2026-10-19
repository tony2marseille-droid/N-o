# -*- coding: utf-8 -*-
"""Pont direct vers le cerveau LocalAI (/api/chat SSE).

Utilise par les clients externes (OpenJarvis) : un seul appel execute le
workflow complet cote LocalAI (routage agent, recherche de reference,
generation + controle qualite, documents, 3D, systeme) et renvoie la
reponse finale avec les chemins des fichiers produits.
"""
import json

import httpx


def tool_cerveau_local(message: str, base_url: str = "") -> str:
    """Execute une demande complete via le cerveau LocalAI.

    message : la demande telle quelle (FR naturel). Retourne la reponse
    finale du cerveau LocalAI (texte), incluant les chemins des fichiers
    produits.
    """
    message = (message or "").strip()[:4000]
    if not message:
        return "Erreur: message vide"
    base = (base_url or "http://127.0.0.1:8320").rstrip("/")
    texte: list[str] = []
    try:
        with httpx.stream(
            "POST",
            f"{base}/api/chat",
            json={
                "message": message,
                "model": "auto",
                "agent_id": "auto",
                "temperature": 0.4,
            },
            timeout=1700,
        ) as r:
            r.raise_for_status()
            for ligne in r.iter_lines():
                if not ligne.startswith("data:"):
                    continue
                try:
                    ev = json.loads(ligne[5:].strip())
                except ValueError:
                    continue
                t = ev.get("type")
                if t == "token":
                    texte.append(ev.get("content", ""))
                elif t == "tool_exec":
                    texte.append(f"\n[outil: {ev.get('name')}]")
                elif t == "error":
                    texte.append(f"\n(erreur: {ev.get('error', '')})")
    except Exception as exc:
        if texte:
            texte.append(f"\n(erreur: {exc})")
        else:
            return f"Erreur cerveau_local: {exc}"
    return "".join(texte).strip() or "(reponse vide)"
