# -*- coding: utf-8 -*-
"""Outils supplementaires (2026-10-19) : memoire persistante, notes,
recherche de fichiers, presse-papiers, conversion d'image.

Meme philosophie que les autres outils : sandbox, chemins absolus sous le
workspace, dependances du venv uniquement.
"""
import re
from pathlib import Path

from backend.core.security import extension_autorisee_ecriture as _extension_bloquee
from backend.core.security import verifier_acces as _verifier_acces, charger_regles as _charger_regles


def _racine_notes() -> str:
    try:
        return str(_charger_regles().get("sandbox_root", r"E:\ia\workspace"))
    except Exception:
        return r"E:\ia\workspace"


def _fichier_notes() -> Path:
    return Path(_racine_notes()) / "notes.md"


# ---- 1. Memoire persistante (table memories â€” injectee dans les prompts) ----

def tool_memoire_ajouter(contenu: str) -> str:
    """Ajoute une information durable a la memoire de l'assistant.

    Les memoires sont injectees dans le contexte de chaque conversation
    (30 dernieres). A utiliser quand l'utilisateur partage une information
    a retenir (preferences, faits, projets).
    """
    contenu = (contenu or "").strip()[:500]
    if not contenu:
        return "Erreur: contenu vide"
    try:
        from backend.core.database import get_db

        db = get_db()
        from datetime import datetime

        now = datetime.now().isoformat(timespec="seconds")
        db.execute(
            "INSERT INTO memories (content, tag, created_at) VALUES (?,?,?)",
            (contenu, "outil", now),
        )
        n = db.query_one("SELECT COUNT(*) AS n FROM memories")
        return f"Memoire enregistree : Â« {contenu[:120]} Â» ({n.get('n', '?')} memoires au total)"
    except Exception as exc:
        return f"Erreur memoire: {exc}"


# ---- 2. Notes persistantes (workspace/notes.md) ----

def tool_notes(action: str = "lire", texte: str = "", titre: str = "") -> str:
    """Notes personnelles persistantes (E:\\ia\\workspace\\notes.md).

    action : 'ecrire' (ajoute titre + texte), 'lire' (les N dernieres), 'effacer'.
    """
    action = (action or "lire").strip().lower()
    p = Path(_fichier_notes())
    if action == "effacer":
        try:
            p.unlink(missing_ok=True)
            return "Notes effacees."
        except Exception as exc:
            return f"Erreur effacement: {exc}"
    if action == "ecrire":
        if not (texte or "").strip():
            return "Erreur: texte vide"
        refus = _verifier_acces(str(p), ecriture=True)
        if refus:
            return refus
        from datetime import datetime

        horodatage = datetime.now().strftime("%Y-%m-%d %H:%M")
        entete = f"\n## {titre.strip() or 'Note'} â€” {horodatage}\n" if titre else f"\n- [{horodatage}] "
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(entete + texte.strip() + "\n")
        return f"Note enregistree dans {p}"
    # lecture : les 30 dernieres lignes non vides
    if not p.is_file():
        return "(aucune note)"
    try:
        lignes = [l for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
        queue = lignes[-30:]
        return "\n".join(queue) or "(aucune note)"
    except Exception as exc:
        return f"Erreur lecture notes: {exc}"


# ---- 3. Recherche de fichiers locaux ----

def tool_rechercher_fichiers(motif: str, dossier: str = "") -> str:
    """Cherche des fichiers par nom (jokers * ?) dans le workspace et les
    documents. Retourne chemin + taille + date, max 25 resultats."""
    motif = (motif or "").strip()
    if not motif:
        return "Erreur: motif vide"
    if "*" not in motif and "?" not in motif:
        motif = f"*{motif}*"
    racine = os.environ.get("LOCALAI_ROOT", r"E:\ia")
    racines = [str(Path(racine) / "workspace"), str(Path(racine) / "data" / "documents")]
    if dossier:
        d = Path(dossier)
        if d.is_dir() and ("workspace" in str(d).lower() or "documents" in str(d).lower()):
            racines = [str(d)]
    resultats: list[str] = []
    for racine in racines:
        r = Path(racine)
        if not r.is_dir():
            continue
        try:
            for f in sorted(r.rglob(motif), key=lambda x: x.stat().st_mtime, reverse=True):
                if not f.is_file():
                    continue
                ko = f.stat().st_size // 1024
                from datetime import datetime

                date = datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d")
                resultats.append(f"{f} ({ko} Ko, {date})")
                if len(resultats) >= 25:
                    break
        except Exception:
            continue
        if len(resultats) >= 25:
            break
    if not resultats:
        return f"Aucun fichier trouvÃ© pour Â« {motif} Â» dans {', '.join(racines)}"
    return "\n".join(resultats)


# ---- 4. Presse-papiers Windows ----

def tool_presse_papiers(action: str = "lire", texte: str = "") -> str:
    """Presse-papiers systeme : 'lire' ou 'ecrire' (texte)."""
    action = (action or "lire").strip().lower()
    import subprocess

    try:
        if action == "ecrire":
            if not (texte or "").strip():
                return "Erreur: texte vide"
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 f"$input | Set-Clipboard"],
                input=texte.encode("utf-8"), capture_output=True, timeout=15,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if proc.returncode != 0:
                return f"Erreur presse-papiers: {proc.stderr.decode(errors='replace')[:200]}"
            return f"Presse-papiers mis a jour ({len(texte)} caracteres)."
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", "Get-Clipboard"],
            capture_output=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if proc.returncode != 0:
            return f"Erreur presse-papiers: {proc.stderr.decode(errors='replace')[:200]}"
        contenu = proc.stdout.decode("utf-8", errors="replace").strip()
        return contenu[:2000] if contenu else "(presse-papiers vide)"
    except Exception as exc:
        return f"Erreur presse-papiers: {exc}"


# ---- 5. Conversion / redimensionnement d'image ----

def tool_convertir_image(source: str, largeur: int = 0, hauteur: int = 0,
                         format_sortie: str = "", destination: str = "") -> str:
    """Redimensionne/convertit une image (PIL). largeur/hauteur 0 = inchangÃ©.
    format_sortie : png | jpg | webp (defaut = celui de la source)."""
    src = Path(str(source).strip())
    if not src.is_file():
        return f"Erreur: image introuvable {source}"
    try:
        from PIL import Image

        with Image.open(src) as im:
            w, h = im.size
            if largeur > 0 and hauteur > 0:
                im = im.resize((int(largeur), int(hauteur)), Image.LANCZOS)
            elif largeur > 0:
                h = max(1, int(h * largeur / w))
                im = im.resize((largeur, h), Image.LANCZOS)
            elif hauteur > 0:
                w = max(1, int(w * hauteur / h))
                im = im.resize((w, hauteur), Image.LANCZOS)
            fmt = (format_sortie or src.suffix.lstrip(".")).lower()
            fmt = {"jpg": "JPEG", "jpeg": "JPEG", "png": "PNG", "webp": "WEBP"}.get(fmt, "PNG")
            dest = Path(destination) if destination else src.with_name(
                f"{src.stem}_conv.{fmt.lower()}")
            refus = _verifier_acces(str(dest), ecriture=True)
            if refus:
                return refus
            if fmt == "JPEG" and im.mode in ("RGBA", "P"):
                im = im.convert("RGB")
            im.save(str(dest), format=fmt)
            ko = dest.stat().st_size // 1024
            return f"Image convertie: {dest} ({ko} Ko, {im.size[0]}x{im.size[1]}, {fmt})"
    except Exception as exc:
        return f"Erreur conversion: {exc}"


# ---- 6. Telechargement de fichier (URL -> workspace/documents) ----

def tool_telecharger_fichier(url: str, destination: str = "") -> str:
    """Telecharge un fichier (PDF, archive, code, image...) vers le dossier
    sandbox. Non-image : contrairement a fetch_image, tout type est accepte."""
    import tempfile

    import httpx

    url = (url or "").strip()
    if not re.match(r"^https?://", url):
        return "Erreur: URL invalide"
    try:
        resp = httpx.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0"},
            timeout=120,
            follow_redirects=True,
        )
        resp.raise_for_status()
        if len(resp.content) < 10:
            return "Erreur: contenu vide"
        nom = url.rstrip("/").split("/")[-1].split("?")[0][:80] or "fichier"
        ext = Path(nom).suffix or ""
        if not ext:
            ctype = resp.headers.get("content-type", "")
            m = re.search(r"/(\w+)", ctype)
            if m:
                ext = "." + m.group(1).replace("plain", "txt")
        dest = destination or str(
            Path(_racine_notes()) / f"{Path(nom).stem}_{int(__import__('time').time())}{ext}"
        )
        refus = _verifier_acces(dest, ecriture=True)
        if refus:
            return refus
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(resp.content)
        return f"Fichier telecharge: {dest} ({len(resp.content) // 1024} Ko)"
    except Exception as exc:
        return f"Erreur telechargement: {exc}"
