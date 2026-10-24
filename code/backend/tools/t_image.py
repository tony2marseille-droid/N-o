# -*- coding: utf-8 -*-
import base64
import os
import re
import shlex
import shutil
import subprocess
import threading
from pathlib import Path

from backend.core.security import (
    commande_autorisee as _commande_bloquee,
    extension_autorisee_ecriture as _extension_bloquee,
    verifier_acces as _verifier_acces,
    charger_regles,
    journaliser,
)
from backend.tools.common import _find_vision_model


def _qc_vision(image_path: str, prompt: str) -> tuple[int, bool]:
    """Controle qualite d'une image generee : note /10 + fidelite a la demande.

    Retourne (note, fidele). En cas d'echec du modele vision, (5, True)
    (aucun blocage).
    """
    q = (
        f"Image demandee : « {prompt[:600]} ». Note cette image sur 10 "
        "(realisme, details, coherence avec la demande) et juge si elle est "
        "FIDELE ou PAS FIDELE a la demande. Reponds exactement : "
        "NOTE: X/10 puis FIDELE ou PAS FIDELE."
    )
    r = tool_analyze_image(image_path, q)
    note, fidele = 5, True
    if not r or "Erreur" in r[:40] or "vide" in r.lower():
        return note, fidele
    m = re.search(r"(\d{1,2})\s*/\s*10", r)
    if m:
        note = max(0, min(10, int(m.group(1))))
    haut = r[:400].upper()
    if "PAS FIDELE" in haut:
        fidele = False
    elif "FIDELE" in haut:
        fidele = True
    return note, fidele


def tool_edit_image(image_path: str, prompt: str, save_path: str = "") -> str:
    """Modifie une image existante (img2img FLUX Kontext) : recoloration,
    changement de decor/ciel, style, suppression d'elements...

    L'image d'entree est conservee ; une nouvelle image modifiee est creee.
    """
    from backend.core.sdgen import _modele_kontext, generer

    p = Path(str(image_path).strip())
    if not p.is_file():
        return f"Erreur: image introuvable {image_path}"
    if not _modele_kontext():
        return (
            "Erreur: le modele FLUX Kontext est requis pour l'edition d'image "
            "(installez flux1-kontext-dev-Q6_K.gguf dans les modeles SD)."
        )
    if not save_path:
        import time as _t

        save_path = str(p.parent / f"edit_{p.stem}_{int(_t.time())}.png")
    if not re.search(r"\.(png|jpe?g|webp)$", save_path.lower()):
        save_path += ".png"
    return generer(prompt, save_path, reference=str(p), mode="edit")


def _vers_maitre(src: Path, dst: Path) -> bool:
    """Convertit le PNG genere en master TIFF (PIL) ou EXR (OpenCV)."""
    try:
        if dst.suffix.lower() in (".tiff", ".tif"):
            from PIL import Image

            im = Image.open(src)
            im.save(dst, format="TIFF", compression="tiff_deflate")
            return dst.is_file()
        # EXR : OpenCV d'abord (si la wheel embarque OpenEXR), puis
        # imageio + FreeImage (telechargement unique au premier appel).
        import os as _os

        _os.environ.setdefault("OPENCV_IO_ENABLE_OPENEXR", "1")
        try:
            import cv2  # type: ignore
            import numpy as _np

            bgr = cv2.imdecode(
                _np.fromfile(str(src), dtype=_np.uint8), cv2.IMREAD_COLOR)
            if bgr is not None:
                ok, buf = cv2.imencode(".exr", bgr.astype(_np.float32))
                if ok:
                    buf.tofile(str(dst))
                    return dst.is_file()
        except Exception:
            pass
        try:
            import imageio.v2 as _imageio
            import numpy as _np2

            from PIL import Image as _Image

            im = _Image.open(src)
            arr = _np2.asarray(im, dtype=_np2.float32)
            _imageio.imwrite(str(dst), arr)
            return dst.is_file()
        except Exception:
            return False
    except Exception:
        return False


def _apercu_jpg(src: Path, dst: Path) -> None:
    """Apercu JPEG du master (affichage navigateur + Telegram)."""
    try:
        from PIL import Image

        im = Image.open(src).convert("RGB")
        im.save(dst, format="JPEG", quality=90, optimize=True)
    except Exception:
        pass


def _finaliser_maitre(src: str, ext: str) -> str | None:
    """PNG -> master TIFF/EXR + apercu JPEG ; retourne le chemin master."""
    p = Path(src)
    cible = p.with_suffix(ext)
    if not _vers_maitre(p, cible):
        return None
    _apercu_jpg(p, cible.with_name(f"{cible.stem}_apercu.jpg"))
    try:
        p.unlink()
    except Exception:
        pass
    return str(cible)


def tool_generate_image(
    prompt: str, save_path: str = "", largeur: int = 0, hauteur: int = 0,
    negatif: str = "", steps: int = 0, reference: str = "",
    reference_desc: str = "", variantes: int = 1, format: str = "",
) -> str:
    """Generation d'image locale via stable-diffusion.cpp.

    largeur/hauteur/steps a 0 => valeurs adaptees au modele (turbo/sdxl/sd15).
    reference : chemin d'une image locale -> fidelite au sujet reel via
    FLUX Kontext (si le modele kontext est installe).
    reference_desc : description visuelle validee du sujet (renforce la
    fidelite du rendu Kontext).
    variantes : nombre d'images a produire (1 a 3) ; chacune est notee par le
    modele vision (note /10 + fidelite) et la meilleure est indiquee en tete.
    format : "tiff" (defaut — master sans perte), "exr" (HDR), "jpeg"/"jpg",
    "png" ou "webp" — definit l'extension et le format de sortie. TIFF/EXR
    produisent aussi un apercu JPEG "_apercu.jpg" cote a cote (affichage
    navigateur + Telegram). Vide => reglage image_format (defaut tiff).
    """
    from backend.core.sdgen import generer

    # Consigne : TIFF par defaut. Le reglage image_format (accueil) prime
    # quand l'agent ne passe pas de format explicite.
    if not format:
        try:
            from backend.core.database import get_db

            row = get_db().query_one(
                "SELECT value FROM app_settings WHERE key=?",
                ("image_format",))
            format = (row["value"] if row else "").strip()
        except Exception:
            format = ""
        if not format:
            format = "tiff"
    fmt = (format or "tiff").lower()
    if fmt in ("jpg", "jpeg"):
        ext = ".jpg"
    elif fmt == "webp":
        ext = ".webp"
    elif fmt == "exr":
        ext = ".exr"
    elif fmt in ("tif", "tiff"):
        ext = ".tiff"
    else:
        fmt = "png"
        ext = ".png"
    # Masters TIFF/EXR : generation intermediaire en PNG (PIL ne sait pas
    # ecrire l'EXR), conversion + apercu JPEG juste apres la generation.
    maitre = ext in (".tiff", ".exr")
    ext_gen = ".png" if maitre else ext

    if not save_path:
        import time as _t

        save_path = str(Path("data") / f"img_gen_{int(_t.time())}{ext_gen}")
    if not re.search(r"\.(png|jpe?g|webp|tiff?|exr)$", save_path.lower()):
        # Force l'extension demandee si l'utilisateur en a mis une autre
        save_path = re.sub(r"\.(png|jpe?g|webp|tiff?|exr)$", ext_gen, save_path, flags=re.I)
        if not save_path.lower().endswith(ext_gen):
            save_path += ext_gen
    elif maitre and not save_path.lower().endswith(".png"):
        save_path = re.sub(r"\.(png|jpe?g|webp|tiff?|exr)$", ".png", save_path, flags=re.I)
        if not save_path.lower().endswith(".png"):
            save_path += ".png"
    elif not maitre and not save_path.lower().endswith(ext):
        # Format resolu != extension du chemin : on harmonise (ex chemin
        # .tiff mais reglage png -> vrai .png ; jamais du PNG dans .tiff).
        save_path = re.sub(r"\.(png|jpe?g|webp|tiff?|exr)$", ext, save_path, flags=re.I)
        if not save_path.lower().endswith(ext):
            save_path += ext

    variantes = max(1, min(3, int(variantes or 1)))

    racine = Path(save_path)
    chemin_v = lambda i: racine if i == 0 else racine.with_name(
        f"{racine.stem}_v{i}{racine.suffix}")

    # 1. Generation (avec retry QC : max 1 regeneration si tout est PAS FIDELE).
    sorties: list[tuple[str, str]] = []
    for i in range(variantes):
        chemin = chemin_v(i)
        msg = generer(prompt, str(chemin), largeur=largeur, hauteur=hauteur,
                      negatif=negatif, steps=steps, reference=reference,
                      reference_desc=reference_desc)
        m = re.match(r"Image generee: (.+?) \(\d+ Ko\)", msg)
        if m and Path(m.group(1)).is_file():
            final = _finaliser_maitre(m.group(1), ext) if maitre else m.group(1)
            if final:
                sorties.append((final, msg))
    if not sorties:
        if maitre:
            return ("Erreur: conversion TIFF/EXR impossible (PIL/OpenCV). "
                    "Reessayez avec format png, jpeg ou webp.")
        return ("Erreur: aucune image generee. Verifiez le generateur "
                "d'images (Reglages > Generation d'images).")

    # 2. QC vision de chaque variante.
    def _evaluer(chemin: str) -> tuple[int, bool]:
        note, fidele = _qc_vision(chemin, prompt)
        return (note, fidele)

    notes: dict[str, tuple[int, bool]] = {}
    for chemin, _ in sorties:
        notes[chemin] = _evaluer(chemin)

    # 3. Retry unique si aucune variante n'est fidele (et <=2 generations).
    if (len(sorties) == 1 and len(notes) == 1
            and not notes[sorties[0][0]][1]):
        chemin2 = chemin_v(1)
        import random

        msg2 = generer(prompt, str(chemin2), largeur=largeur, hauteur=hauteur,
                       negatif=negatif, steps=steps, reference=reference,
                       reference_desc=reference_desc,
                       graine=random.randint(1, 2**31 - 1))
        m2 = re.match(r"Image generee: (.+?) \(\d+ Ko\)", msg2)
        if m2 and Path(m2.group(1)).is_file():
            final2 = _finaliser_maitre(m2.group(1), ext) if maitre else m2.group(1)
            if final2:
                sorties.append((final2, msg2))
                notes[final2] = _evaluer(final2)

    # 4. Meilleure : fidele d'abord, puis note.
    def _cle(chemin: str) -> tuple[int, int]:
        note, fidele = notes[chemin]
        return (1 if fidele else 0, note)

    best = max((c for c, _ in sorties), key=_cle)
    bnote, bfidele = notes[best]
    infos = [f"MEILLEURE IMAGE: {best} (note {bnote}/10, "
             f"{'FIDELE' if bfidele else 'PAS FIDELE'})"]
    for chemin, _ in sorties:
        if chemin != best:
            n, f = notes[chemin]
            infos.append(f"Variante: {chemin} (note {n}/10, "
                         f"{'FIDELE' if f else 'PAS FIDELE'})")
    return "\n".join(infos)



def tool_image_search(query: str) -> str:
    """Recherche d'images de reference : Wikipedia (personnes/sujets reels),
    Wikimedia Commons, Openverse, DuckDuckGo.

    L'UA doit etre CONFORME a la politique Wikimedia (identite + contact),
    sinon reponse 403 silencieuse. Retourne titres + URL d'images
    telechargeables pour le modele vision ou fetch_image.
    """
    import html as _html

    import httpx

    query = (query or "").strip()[:200]
    if not query:
        return "Erreur: requete vide"
    ua = {"User-Agent": "NeoLocalAI/1.0 (assistant IA personnel; contact: neo@localai.local)"}
    out: list[str] = []

    # 0. Wikipedia pageimages : LA source fiable pour les personnes et
    #    sujets notoires (photo officielle de l'article). fr puis en.
    for lang in ("fr", "en"):
        try:
            r = httpx.get(
                f"https://{lang}.wikipedia.org/w/api.php",
                params={
                    "action": "query", "format": "json", "redirects": 1,
                    "prop": "pageimages", "piprop": "thumbnail",
                    "pithumbsize": 800, "titles": query,
                },
                headers=ua, timeout=12,
            )
            if r.status_code != 200:
                continue
            pages = r.json().get("query", {}).get("pages", {})
            for p in pages.values():
                src = p.get("thumbnail", {}).get("source", "")
                if src:
                    titre = _html.unescape(p.get("title", query))[:70]
                    out.append(f"{len(out)+1}. [wikipedia] {titre}\n   IMAGE: {src}")
                    break
            if out:
                break
        except Exception:
            continue

    try:
        r = httpx.get(
            "https://commons.wikimedia.org/w/api.php",
            params={
                "action": "query",
                "generator": "search",
                "gsrsearch": f"{query} filetype:bitmap",
                "gsrlimit": 5,
                "gsrnamespace": 6,
                "prop": "imageinfo",
                "iiprop": "url",
                "format": "json",
            },
            headers=ua,
            timeout=15,
        )
        pages = list(r.json().get("query", {}).get("pages", {}).values())
        pages.sort(key=lambda p: p.get("index", 99))
        for p in pages[:4]:
            ii = p.get("imageinfo", [{}])[0]
            url = ii.get("url", "")
            if url:
                title = p.get("title", "").replace("File:", "")[:70]
                out.append(f"{len(out)+1}. [wikimedia] {title}\n   IMAGE: {url}")
    except Exception:
        pass

    try:
        r = httpx.get(
            "https://api.openverse.org/v1/images/",
            params={"q": query, "page_size": 3},
            headers=ua,
            timeout=15,
        )
        if r.status_code == 200:
            for i, x in enumerate(r.json().get("results", [])[:2]):
                title = _html.unescape(str(x.get("title", "")))[:70]
                url = x.get("url") or x.get("thumbnail")
                if url:
                    out.append(f"{len(out)+1}. [openverse] {title}\n   IMAGE: {url}")
    except Exception:
        pass

    try:
        client = httpx.Client(
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36",
                "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
            },
            timeout=14,
            follow_redirects=True,
        )
        home = client.get(
            "https://duckduckgo.com/",
            params={"q": query, "iax": "images", "ia": "images"},
        )
        m = re.search(r"vqd=['\"]?([\d-]+)", home.text)
        if m:
            r2 = client.get(
                "https://duckduckgo.com/i.js",
                params={"l": "fr-fr", "o": "json", "q": query, "vqd": m.group(1), "f": ",,,", "p": "1"},
                headers={"Referer": "https://duckduckgo.com/"},
            )
            for item in r2.json().get("results", [])[:3]:
                title = _html.unescape(re.sub(r"<[^>]+>", "", item.get("title", "")))[:70]
                if item.get("image"):
                    out.append(f"{len(out)+1}. [ddg] {title}\n   IMAGE: {item['image']}")
    except Exception:
        pass

    return "\n\n".join(out) if out else (
        "Aucune image trouvee pour cette recherche. Essaie : (1) une "
        "formulation plus precise du sujet, (2) web_search pour trouver "
        "manuellement l'URL d'une image, puis fetch_image dessus, ou "
        "(3) decris ce que tu cherches si c'est un concept abstrait."
    )



def tool_fetch_image(url: str, save_path: str = "") -> str:
    """Telecharge une image et retourne le chemin local."""
    import tempfile
    import time

    import httpx

    url = (url or "").strip()
    if not re.match(r"^https?://", url):
        return "Erreur: URL invalide"
    try:
        resp = httpx.get(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0",
                "Referer": "https://duckduckgo.com/",
            },
            timeout=20,
            follow_redirects=True,
        )
        ctype = resp.headers.get("content-type", "")
        if not ctype.startswith("image/") and len(resp.content) < 1000:
            return f"Erreur: contenu non image ({ctype})"
        ext = ".jpg"
        m = re.search(r"image/(\w+)", ctype)
        if m:
            ext = {"jpeg": ".jpg", "png": ".png", "webp": ".webp", "gif": ".gif"}.get(m.group(1), ".jpg")
        path = save_path or str(Path(tempfile.gettempdir()) / f"localai_img_{int(time.time())}{ext}")
        refus = _verifier_acces(path, ecriture=True)
        if refus:
            return refus
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(resp.content)
        kb = len(resp.content) // 1024
        return f"Image telechargee: {path} ({kb} Ko)"
    except Exception as exc:
        return f"Erreur telechargement: {exc}"



def tool_analyze_image(image_path: str, question: str) -> str:
    """Analyse une image locale avec un modele vision (validation visuelle)."""
    import base64 as b64
    import time

    import httpx

    p = Path(str(image_path).strip())
    if not p.exists():
        return f"Erreur: image introuvable {image_path}"
    if not question:
        question = "Decris cette image en detail."
    model = _find_vision_model()
    if not model:
        return (
            "Erreur: aucun modele vision installe. Installez par exemple "
            "`ollama pull qwen2.5vl:7b` ou utilisez gemma3 (multimodal)."
        )
    data = b64.b64encode(p.read_bytes()).decode()
    # Les images 4K (>1500px) depassent le budget vision : on reduit.
    try:
        from PIL import Image as _Image

        with _Image.open(p) as im:
            if max(im.size) > 1536:
                import tempfile

                im.thumbnail((1536, 1536), _Image.LANCZOS)
                tmp = Path(tempfile.mkdtemp(prefix="vis_")) / "v.png"
                im.save(tmp, format="PNG")
                data = b64.b64encode(tmp.read_bytes()).decode()
    except Exception:
        pass
    base = os.environ.get("LOCALAI_OLLAMA_URL", "http://127.0.0.1:11434")
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": question[:1500], "images": [data]}],
        "stream": False,
        "keep_alive": "15m",
        "options": {"temperature": 0.2, "num_ctx": 4096},
    }
    text = ""
    for tentative in range(3):
        try:
            resp = httpx.post(f"{base}/api/chat", json=payload, timeout=240)
            text = resp.json().get("message", {}).get("content", "") or ""
        except Exception:
            text = ""
        if text.strip():
            return f"[{model}] {text.strip()[:1200]}"
        # Swap de modeles VRAM en cours (le LLM principal est charge) :
        # on recharge et on reessaie apres un court delai.
        try:
            httpx.post(f"{base}/api/generate",
                       json={"model": model, "keep_alive": 0}, timeout=10)
        except Exception:
            pass
        time.sleep(6 + 6 * tentative)
    return f"[{model}] Reponse vide du modele vision (apres 3 essais)."



def _search_reference_urls(query: str) -> list[str]:
    """Recherche + degradation automatique de la requete jusqu'a trouver des images."""
    variantes = [query]
    mots = query.split()
    if len(mots) > 2:
        variantes.append(" ".join(mots[:2]))
    if len(mots) > 1:
        variantes.append(mots[0])
        variantes.append(f"{mots[0]} portrait")
    vues: set[str] = set()
    for variante in variantes:
        search = tool_image_search(variante)
        urls = [
            line.replace("   IMAGE: ", "").split("?utm_source")[0]
            for line in search.split("\n")
            if line.strip().startswith("IMAGE:")
        ]
        for u in urls:
            if u not in vues:
                vues.add(u)
        if len(vues) >= 3:
            break
    return list(vues)



# Mots a ignorer pour extraire le SUJET d'une requete utilisateur (« genere
# moi une image photorealiste 4k d un avion SR71... » -> « avion sr71 ... »).
_STOPWORDS_REF = {
    "genere", "generez", "genere-moi", "generez-moi", "creer", "cree", "crée",
    "dessine", "dessiner", "fais", "fait", "faire", "montre", "moi", "me", "je",
    "tu", "il", "elle", "on", "nous", "vous", "veux", "voudrais", "peux", "peut",
    "aimerais", "jaimerais", "une", "un", "des", "d", "du", "de", "la", "le",
    "les", "l", "a", "au", "aux", "avec", "dans", "sur", "sous", "et", "ou",
    "que", "qui", "est", "sont", "en", "au", "pour", "pas", "ne", "plus",
    "moins", "image", "images", "photo", "photos", "photorealiste", "realiste",
    "réaliste", "4k", "hd", "hdr", "8k", "2k", "volant", "qui", "style",
    "couleur", "couleurs", "fond", "beau", "belle", "joli", "jolie",
    "magnifique", "superbe", "petit", "petite", "grand", "grande", "tres",
    "très", "super", "quelque", "comme", "aujourdhui", "aujourd'hui",
}


def _extraire_sujet(requete: str) -> list[str]:
    """Extraire les mots-clefs du sujet : stopwords retires, ordre conserve.

    Retourne une liste de requetes candidates, de la plus complete a la plus
    courte (degradation progressive pour la recherche d'images).
    """
    import re

    mots = re.findall(r"[a-z0-9àâçéèêëîïôùûüÿ'’-]+", requete.lower())
    mots = [m for m in mots if m not in _STOPWORDS_REF]
    if not mots:
        return [requete.strip()]
    sujets: list[str] = []
    # Candidats : requete complete puis noyau du sujet (3/2/1 premiers mots).
    for n in (len(mots), 3, 2, 1):
        if n <= len(mots):
            s = " ".join(mots[:n])
            if s.lower() not in [x.lower() for x in sujets]:
                sujets.append(s)
    # Degradations intermediaires (fin retiree mot a mot).
    for n in range(len(mots) - 1, 3, -1):
        s = " ".join(mots[:n])
        if s.lower() not in [x.lower() for x in sujets]:
            sujets.append(s)
    return sujets


def tool_find_reference_image(object_query: str) -> str:
    """Agent de recherche d'image intelligent : degrade progressivement la requete
    (description complete -> sujet court) jusqu'a trouver une photo validee par le
    modele vision. Retourne le chemin de la meilleure reference + sa description.
    """
    object_query = (object_query or "").strip()[:200]
    if not object_query:
        return "Erreur: description d'objet vide"
    # Le modele vision doit pouvoir charger en VRAM : on decharge le LLM
    # principal (il se rechargera a la prochaine requete, RAM 48 Go dispo).
    try:
        from backend.core.sdgen import _liberer_vram

        _liberer_vram()
    except Exception:
        pass
    sujets = _extraire_sujet(object_query)
    report: list[str] = []
    validations = 0
    MAX_VALIDATIONS = 4
    best = None
    best_desc = ""
    for sujet in sujets:
        if best or validations >= MAX_VALIDATIONS:
            break
        search = tool_image_search(sujet)
        urls = [
            line.replace("   IMAGE: ", "").split("?utm_source")[0]
            for line in search.split("\n")
            if line.strip().startswith("IMAGE:")
        ]
        if not urls:
            continue
        for url in urls[:4]:
            if best or validations >= MAX_VALIDATIONS:
                break
            fetched = tool_fetch_image(url)
            m = re.match(r"Image telechargee: (.+?) \((\d+) Ko\)", fetched)
            if not m:
                continue
            path = m.group(1)
            # Reference legere (max 1024 px) : moins de charge vision/Kontext.
            try:
                from PIL import Image as _Image

                with _Image.open(path) as im:
                    if max(im.size) > 1024:
                        im.thumbnail((1024, 1024), _Image.LANCZOS)
                        im.save(path)
            except Exception:
                pass
            validations += 1
            desc = tool_analyze_image(
                path,
                f"L'utilisateur cherche des references visuelles de : « {sujet} ».\n"
                "IMPORTANT : reponds NON-PERTINENT si le sujet principal est AUTRE CHOSE "
                "(personne differente, objet different, lieu different, scene sans le sujet). "
                "Si le sujet principal de l'image EST bien ce sujet, reponds PERTINENT.\n"
                "Reponds sur la premiere ligne par exactement PERTINENT ou NON-PERTINENT, puis "
                "decris ses caracteristiques visuelles distinctives en ANGLAIS (une phrase : "
                "forme, proportions, couleurs, details). Si le sujet est une PERSONNE, une "
                "photo de son visage de face ou de profil est PERTINENTE.",
            )
            ok = (
                "PERTINENT" in desc[:200].upper()
                and "NON-PERTINENT" not in desc[:200].upper()
                and desc[:200].upper().count("PERTINENT") >= 1
                and not desc[:200].upper().startswith("NON")
            )
            tag = "OK " if ok else "-- "
            report.append(f"[{tag}] {Path(path).name} ({sujet}): {desc[:300]}")
            if ok:
                best = path
                best_desc = desc[:600]
    if best:
        return "\n".join(
            [
                f"MEILLEURE REFERENCE: {best}",
                f"Description: {best_desc}",
                "Pour une forme organique (visage, personnage, sculpture), utilise "
                "MAINTENANT generate_mesh_from_image sur cette reference (3D). "
                "Pour une IMAGE 2D fidele au sujet (avion, vehicule, animal...), "
                "utilise MAINTENANT generate_image avec reference=<ce chemin>.",
                "",
                "DETAILS DE LA RECHERCHE:",
                *report,
            ]
        )
    return "\n\n".join(
        report
        + [
            "\nAUCUNE image pertinente trouvee. Base-toi sur tes connaissances "
            "geometriques et precise les caracteristiques distinctives du sujet."
        ]
    )
