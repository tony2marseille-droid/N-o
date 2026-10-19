"""Generation d'images locale via stable-diffusion.cpp.

Meme philosophie que TripoSR : binaire et modeles isoles dans un dossier
outils (defaut E:\\ia\\tools\\sd, surcharge par LOCALAI_SD_DIR), aucune
dependance python lourde dans l'application principale.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

from backend.core.security import extension_autorisee_ecriture as _extension_bloquee
from backend.core.security import verifier_acces as _verifier_acces

# Negatif par defaut (anglais : SD comprend mieux). Termes anti-« dessin »
# pour forcer le photorealisme (les modeles rapides donnent sinon un rendu
# enfantin/plastique).
DEFAULT_NEGATIF = (
    "lowres, blurry, out of focus, watermark, signature, text, logo, "
    "jpeg artifacts, worst quality, low quality, deformed, disfigured, "
    "bad anatomy, bad hands, extra fingers, extra limbs, duplicate, cropped, "
    "drawing, painting, sketch, cartoon, anime, illustration, comic, "
    "3d render, cgi, plastic, toy, kid drawing, childish, amateur"
)

# Suffixe de qualite ajoute au prompt (anglais) — vise un rendu haut de gamme.
SUFFIXE_QUALITE = ("masterpiece, best quality, ultra detailed, sharp focus, "
                   "vivid colors, professional, trending on artstation")

# Suffixe REALISTE : vise le photorealisme 2D (defaut d'une demande d'image).
SUFFIXE_REALISME = ("masterpiece, best quality, ultra detailed, photorealistic, "
                    "realistic photograph, cinematic lighting, hdr, 8k, "
                    "sharp focus, fine detail")
# Mots indiquant un STYLE graphique -> on n'impose pas le photorealisme.
STYLE_MOTS = ("cartoon", "manga", "anime", "dessin", "croquis", "aquarelle",
              "peinture", "pixel", "logo", "comics", "b d ", "stylise",
              "stylisé", "vectoriel", "abstrait", "cel-shad", "render 3d",
              "low poly", "affiche")


def _dir() -> Path:
    return Path(os.environ.get("LOCALAI_SD_DIR", r"E:\ia\tools\sd"))


def _exe() -> Path:
    d = _dir()
    candidats = list((d / "bin").glob("sd*.exe")) + list(d.glob("sd*.exe"))
    return candidats[0] if candidats else d / "bin" / "sd.exe"


def _modele() -> Path:
    # Surcharge explicite (ex. LOCALAI_SD_MODEL=flux1-schnell-Q8_0.gguf).
    force = os.environ.get("LOCALAI_SD_MODEL", "").strip()
    if force:
        cand = _dir() / "models" / force
        if cand.is_file():
            return cand
    d = _dir() / "models"
    modeles = [m for m in (sorted(d.glob("*.gguf")) + sorted(d.glob("*.safetensors")))
               if m.is_file()]

    def _rang(m: Path) -> int:
        n = m.name.lower()
        if n.startswith(("t5xxl", "clip_l", "clip_g", "ae.", "vae")):
            return 99  # encodeurs/VAE seuls
        if "kontext" in n:
            # Exige une image de reference -> jamais choisi par defaut
            # (selectionne explicitement par generer(..., reference=...)).
            return 99
        if "flux" in n:
            # utilisable seulement si le t5xxl est present ; preferer les
            # quantifications legeres (tiennent en VRAM, rapides) aux lourdes.
            t5 = any("t5xxl" in f.name.lower() for f in d.iterdir() if f.is_file())
            if not t5:
                return 99
            if "q4" in n or "q5" in n:
                return 0
            return 1
        if "realvis" in n or "juggernaut" in n or "realistic" in n:
            # SDXL UNet seul : exige clip_l ET clip_g ET vae separees.
            noms = {f.name.lower() for f in d.iterdir() if f.is_file()}
            complet = (any("clip_l" in x for x in noms)
                       and any("clip_g" in x for x in noms)
                       and any("vae" in x or x.startswith("ae.") for x in noms))
            return 2 if complet else 99
        if "xl" in n:
            return 3
        return 20 if ("turbo" in n or "lcm" in n or "lightning" in n) else 10

    modeles = [m for m in modeles if _rang(m) < 90]
    if not modeles:
        return d / "_absent.gguf"
    return sorted(modeles, key=_rang)[0]


def _type_modele(m: Path | None = None) -> str:
    """Classe le modele SD : 'flux', 'turbo', 'sdxl' ou 'sd15'."""
    m = m or _modele()
    n = m.name.lower()
    if "flux" in n:
        return "flux"
    if "turbo" in n or "lcm" in n or "lightning" in n:
        return "turbo"
    if "xl" in n or "realvis" in n or "juggernaut" in n or "realistic" in n:
        return "sdxl"
    return "sd15"


def _steps_defaut(m: Path | None = None) -> int:
    """Nombre de steps adapte au modele (flux schnell=4, dev=28 ; sdxl=28…)."""
    n = (_modele() if m is None else m).name.lower()
    if "kontext" in n:
        return 20
    if "flux" in n:
        return 4 if "schnell" in n else 28
    return {"turbo": 8, "sdxl": 28, "sd15": 28}.get(_type_modele(m), 28)


def _resolution_defaut(m: Path | None = None) -> int:
    """Cote par defaut adapte au modele (carre)."""
    return {"flux": 1024, "turbo": 768, "sdxl": 1024, "sd15": 512}.get(
        _type_modele(m), 512)


def status() -> dict:
    exe = _exe()
    modele = _modele()
    t = _type_modele(modele)
    return {
        "dir": str(_dir()),
        "binaire": exe.exists(),
        "modele": str(modele) if modele.is_file() else "",
        "pret": exe.exists() and modele.is_file(),
        "type": t,
        "steps_defaut": _steps_defaut(modele),
        "resolution_defaut": _resolution_defaut(modele),
    }


def _liberer_vram() -> None:
    """Decharge les modeles Ollama pour liberer la VRAM avant la generation.

    Best-effort ; les 48 Go de RAM restent disponibles pour l'offload CPU des
    gros modeles (FLUX). Le LLM se rechargera a la prochaine requete.
    """
    import httpx

    try:
        base = os.environ.get("LOCALAI_OLLAMA_URL", "http://127.0.0.1:11434")
        ps = httpx.get(f"{base}/api/ps", timeout=5).json().get("models", [])
        for m in ps:
            nom = m.get("name", "")
            if nom:
                httpx.post(f"{base}/api/generate",
                           json={"model": nom, "keep_alive": 0}, timeout=10)
    except Exception:
        pass


def _est_francais(texte: str) -> bool:
    """Heuristique : le prompt contient-il des marqueurs francais ?"""
    t = " " + texte.lower() + " "
    marqueurs = (
        " un ", " une ", " des ", " le ", " la ", " les ", " de ", " du ",
        " avec ", " dans ", " sur ", " couleur", " fond ", " vue ", " ciel ",
        " planete", " planetes", " forêt", " foret", " maison ", " chat ",
        "à ", "é", "è", "ê", "â", "î", "ô", "û", "ç", "œ",
    )
    return any(m in t for m in marqueurs)


def _traduire_enrichir(prompt: str) -> str:
    """Traduit (FR->EN) et enrichit le prompt via le LLM local. Best-effort :
    en cas d'echec, retourne le prompt original inchange.

    Optimisation : reutilise un modele DEJA charge dans Ollama (evite un
    cycle de chargement/déchargement d'une minute pour chaque generation).
    """
    import httpx

    base = os.environ.get("LOCALAI_OLLAMA_URL", "http://127.0.0.1:11434")
    modele = os.environ.get("LOCALAI_SD_TRAD_MODELE", "")
    if not modele:
        # Un modele deja en RAM ? (le champion du chat est ideal — zero load)
        try:
            ps = httpx.get(f"{base}/api/ps", timeout=5).json().get("models", [])
            for m in ps:
                nom = m.get("name", "")
                if nom and "vl" not in nom and "vision" not in nom and "llava" not in nom:
                    modele = nom
                    break
        except Exception:
            pass
        modele = modele or "qwen3:8b"
    inst = (
        "You are a prompt engineer for Stable Diffusion. Translate the following "
        "image description to English and enrich it with visual detail (subject, "
        "composition, lighting, style, colors). Reply with ONE single line: only "
        "the final English prompt, no quotes, no explanation.\n"
        f"Description: {prompt}"
    )
    try:
        r = httpx.post(
            f"{base}/api/generate",
            json={
                "model": modele,
                "prompt": inst,
                "stream": False,
                "think": False,
                "options": {"temperature": 0.3, "num_predict": 160},
            },
            timeout=25,
        )
        txt = (r.json().get("response") or "").strip().strip('"').strip()
        txt = txt.splitlines()[0].strip() if txt else ""
        if 3 <= len(txt) <= 400:
            return txt
    except Exception:
        pass
    return prompt


def preparer_prompt(prompt: str, traduire: bool = True, reference_desc: str = "") -> str:
    """Prepare le prompt final : traduction/enrichissement + suffixe.

    Par defaut, une image vise le PHOTOREALISME 2D. Si l'utilisateur demande un
    style (cartoon, dessin, logo...), on n'impose pas le realisme.
    """
    p = (prompt or "").strip()
    if not p:
        return p
    bas = p.lower()
    stylise = any(w in bas for w in STYLE_MOTS)
    # Fidelite Kontext : le contexte de reference est deja en anglais (la
    # description vient du modele vision) — la traduction n'apporte rien et
    # coute un cycle de chargement LLM. On saute.
    if traduire and _est_francais(p) and not reference_desc:
        p = _traduire_enrichir(p)
    suffixe = SUFFIXE_QUALITE if stylise else SUFFIXE_REALISME
    # Evite de dupliquer le suffixe si l'appelant l'a deja mis.
    if suffixe.split(",")[0] not in p.lower():
        p = f"{p}, {suffixe}"
    return p


# ---- Upscale 4K (Real-ESRGAN + repli Lanczos) ----


def _esrgan_exe() -> Path | None:
    d = Path(os.environ.get("LOCALAI_ESRGAN_DIR", r"E:\ia\tools\RealESRGAN"))
    if not d.is_dir():
        return None
    for p in sorted(d.glob("realesrgan-ncnn-vulkan*.exe")):
        if p.is_file():
            return p
    return None


def _resize_lanczos(src: Path, dst: Path, largeur: int, hauteur: int) -> bool:
    try:
        from PIL import Image

        im = Image.open(src).convert("RGB")
        im.thumbnail((largeur, hauteur), Image.LANCZOS)
        im.save(dst, format="PNG")
        return dst.is_file()
    except Exception:
        return False


def _upscale_4k(src: Path, dst: Path) -> bool:
    """Upscale x4 (Real-ESRGAN x4plus) puis ajuste a 3840x2160.

    Retourne False si l'upscaler n'est pas disponible/echec.
    """
    exe = _esrgan_exe()
    if not exe:
        return False
    import tempfile

    try:
        tmpdir = Path(tempfile.mkdtemp(prefix="esrgan_"))
        tmpout = tmpdir / "out.png"
        cmd = [str(exe), "-i", str(src), "-o", str(tmpout),
               "-s", "4", "-n", "realesrgan-x4plus", "-f", "png"]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if proc.returncode != 0 or not tmpout.is_file():
            return False
        return _resize_lanczos(tmpout, dst, 3840, 2160)
    except Exception:
        return False


def installer(binaire_seulement: bool = False) -> dict:
    """Telecharge stable-diffusion.cpp (release Windows la plus recente)
    puis un modele SD 1.5 GGUF via les APIs GitHub/HuggingFace."""
    import zipfile

    import httpx

    d = _dir()
    (d / "bin").mkdir(parents=True, exist_ok=True)
    (d / "models").mkdir(parents=True, exist_ok=True)

    # 1. binaire : derniere release GitHub, asset windows (cuda > vulkan > cpu)
    rel = httpx.get(
        "https://api.github.com/repos/leejet/stable-diffusion.cpp/releases/latest",
        timeout=30,
    ).json()
    assets = [
        a
        for a in rel.get("assets", [])
        if "-win-" in a["name"].lower() and a["name"].endswith(".zip")
    ]
    asset = next((a for a in assets if "cuda12" in a["name"].lower()), None) or next(
        (a for a in assets if "vulkan" in a["name"].lower()), None
    ) or next((a for a in assets if "cpu" in a["name"].lower()), None)
    if not asset:
        return {"ok": False, "error": "Aucun binaire Windows trouve dans la release"}
    zip_path = d / asset["name"]
    with httpx.stream("GET", asset["browser_download_url"], timeout=600, follow_redirects=True) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        fait = 0
        with zip_path.open("wb") as f:
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
                fait += len(chunk)
                print(f"[sd-install] binaire {fait // 1048576}/{total // 1048576} Mo")
    extrait = d / "_extract"
    shutil.rmtree(extrait, ignore_errors=True)
    extrait.mkdir(parents=True)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(extrait)
    for src in list(extrait.rglob("*.exe")) + list(extrait.rglob("*.dll")):
        shutil.copy2(src, d / "bin" / src.name)
    shutil.rmtree(extrait, ignore_errors=True)
    zip_path.unlink(missing_ok=True)

    # 1b. runtimes CUDA si variante cuda (DLLs separees dans la release)
    if asset and "cuda" in asset["name"].lower():
        cudart = next(
            (
                a
                for a in rel.get("assets", [])
                if a["name"].lower().startswith("cudart-sd-bin-win")
            ),
            None,
        )
        if cudart:
            zip_cudart = d / cudart["name"]
            with httpx.stream(
                "GET", cudart["browser_download_url"], timeout=900, follow_redirects=True
            ) as r:
                r.raise_for_status()
                with zip_cudart.open("wb") as f:
                    for chunk in r.iter_bytes(1 << 20):
                        f.write(chunk)
            extrait = d / "_extract"
            shutil.rmtree(extrait, ignore_errors=True)
            extrait.mkdir(parents=True)
            with zipfile.ZipFile(zip_cudart) as z:
                z.extractall(extrait)
            for src in list(extrait.rglob("*.dll")) + list(extrait.rglob("*.exe")):
                shutil.copy2(src, d / "bin" / src.name)
            shutil.rmtree(extrait, ignore_errors=True)
            zip_cudart.unlink(missing_ok=True)

    if binaire_seulement:
        return {"ok": True, "etape": "binaire installe"}

    # 2. modele : recherche HF API d'un checkpoint SD 1.5 fichier unique
    fichier_choisi = None
    repo_choisi = None
    try:
        req = httpx.get(
            "https://huggingface.co/api/models",
            params={"search": "v1-5-pruned-emaonly", "limit": 20, "full": "true"},
            timeout=30,
        ).json()
        motif = re.compile(r"(v1[-._]?5|sd.?1[.-]?5).*\.gguf$", re.I)
        for repo in req:
            for sib in repo.get("siblings", []):
                nom = sib.get("rfilename", "")
                if nom.endswith(".gguf") and motif.search(nom):
                    taille_preferee = "q8" in nom.lower() or "q5" in nom.lower() or "f16" in nom.lower()
                    if taille_preferee or not fichier_choisi:
                        fichier_choisi = nom
                        repo_choisi = repo.get("id")
                    if fichier_choisi:
                        break
            if fichier_choisi:
                break
    except Exception:
        pass
    if not fichier_choisi:
        # repli verifie (decouvert via API HF) : checkpoint officiel SD 1.5
        repo_choisi = "stable-diffusion-v1-5/stable-diffusion-v1-5"
        fichier_choisi = "v1-5-pruned-emaonly.safetensors"
    url_modele = f"https://huggingface.co/{repo_choisi}/resolve/main/{fichier_choisi}"
    dest = d / "models" / fichier_choisi
    with httpx.stream("GET", url_modele, timeout=3600, follow_redirects=True) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        fait = 0
        with dest.open("wb") as f:
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
                fait += len(chunk)
                print(f"[sd-install] modele {fait // 1048576}/{max(total, 1) // 1048576} Mo")
    return {"ok": True, "binaire": str(_exe()), "modele": str(dest)}


def _modele_kontext() -> Path | None:
    """Modele FLUX Kontext (fidelite sujet via image de reference), ou None."""
    d = _dir() / "models"
    for f in sorted(d.glob("*.gguf")):
        if "kontext" in f.name.lower() and f.is_file():
            return f
    return None


def _prechauffer_cache(chemins: list[str]) -> None:
    """Prechauffe le cache fichier Windows pour les gros modeles.

    stable-diffusion.cpp charge via mmap (faults 4K quasi-aleatoires) : sur
    un SSD QLC froid, cela tombe a ~20 Mo/s (10 min de chargement). Une
    lecture sequentielle prealable remplit le cache a ~290 Mo/s (~45 s pour
    12 Go), puis les faults de sd-cli touchent la RAM. Best-effort.
    """
    import time as _t

    for p in chemins:
        try:
            path = Path(p)
            if not path.is_file() or path.stat().st_size < 100 * 1024 * 1024:
                continue
            t0 = _t.time()
            with open(path, "rb") as f:
                while f.read(8 * 1024 * 1024):
                    pass
            print(f"[sd-warm] {path.name} prechauffe en {_t.time() - t0:.0f}s")
        except Exception:
            continue


def _acquerir_verrou_gpu(attente_s: int = 3600):
    """Verrou GLOBAL inter-processus : une seule generation SD a la fois.

    Chaque generation peut charger ~10 Go (VRAM + RAM) ; deux demandes
    concurrentes finiraient en OOM. Le verrou est un fichier locke via
    msvcrt (Windows) / fcntl (POSIX) ; retourne le descripteur a garder
    ouvert (le verrou est libere a la fermeture).
    """
    import time

    lock = Path(
        os.environ.get("LOCALAI_LOCK_DIR", os.environ.get("TEMP", r"C:\Windows\Temp"))
    ) / "localai_sd.lock"
    debut = time.time()
    while True:
        try:
            f = open(lock, "a+")
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return f
        except (OSError, IOError):
            try:
                f.close()
            except Exception:
                pass
            if time.time() - debut > attente_s:
                return None
            time.sleep(4)


def generer(
    prompt: str,
    sortie: str,
    negatif: str = "",
    largeur: int = 0,
    hauteur: int = 0,
    steps: int = 0,
    graine: int = -1,
    traduire: bool = True,
    resolution: str = "4k",
    reference: str = "",
    reference_desc: str = "",
    mode: str = "creation",
) -> str:
    """Genere une image (defaut : 4K 3840x2160, photorealiste). Retourne un
    message outil (chemin PNG ou erreur).

    Une seule generation a la fois (verrou global) : file d'attente implicite,
    evite les OOM quand deux demandes d'image arrivent en parallele.
    mode="edit" : modifie l'image de reference (img2img Kontext) au lieu de
    recréer une scene autour de son sujet.
    """
    lock = _acquerir_verrou_gpu()
    if lock is None:
        return (
            "Erreur: le generateur d'images est occupe par une autre demande "
            "depuis plus d'une heure. Reessayez plus tard."
        )
    try:
        return _generer_nolock(
            prompt, sortie, negatif=negatif, largeur=largeur, hauteur=hauteur,
            steps=steps, graine=graine, traduire=traduire, resolution=resolution,
            reference=reference, reference_desc=reference_desc, mode=mode,
        )
    finally:
        try:
            lock.close()
        except Exception:
            pass


def _generer_nolock(
    prompt: str,
    sortie: str,
    negatif: str = "",
    largeur: int = 0,
    hauteur: int = 0,
    steps: int = 0,
    graine: int = -1,
    traduire: bool = True,
    resolution: str = "4k",
    reference: str = "",
    reference_desc: str = "",
    mode: str = "creation",
) -> str:
    """Genere une image (defaut : 4K 3840x2160, photorealiste). Retourne un
    message outil (chemin PNG ou erreur).

    Pipeline : generation SD en base 16:9 (1024x576, cout VRAM identique a
    768x768) puis upscale x4 Real-ESRGAN (repli : Lanczos) vers 3840x2160.
    resolution="hd" desactive l'upscale (image base directe).

    reference : chemin d'une image locale de reference. Si un modele FLUX
    Kontext est installe, il est utilise (le sujet de la reference est
    preserve : forme, couleurs, proportions fidele au vrai sujet).
    """
    st = status()
    if not st["pret"]:
        return (
            "Generateur d'images non installe. Installez-le depuis "
            "Reglages > Generation d'images (bouton Installer), ou definissez "
            "LOCALAI_SD_DIR vers un dossier contenant bin/sd.exe et models/*.gguf."
        )
    refus_ext = _extension_bloquee(sortie)
    if refus_ext:
        return refus_ext
    refus = _verifier_acces(sortie, ecriture=True)
    if refus:
        return refus
    out_final = Path(sortie).expanduser().resolve()
    out_final.parent.mkdir(parents=True, exist_ok=True)
    out = out_final

    modele = _modele()
    t = _type_modele(modele)
    ref_ok = Path(reference).is_file() if reference else False
    if ref_ok:
        # Fidelite sujet : FLUX Kontext conditionne par l'image de reference.
        kontext = _modele_kontext()
        if kontext:
            modele = kontext
            t = "flux"
    if largeur <= 0:
        largeur = _resolution_defaut(modele)
    if hauteur <= 0:
        hauteur = _resolution_defaut(modele)
    if steps <= 0:
        steps = _steps_defaut(modele)
    # Garde-fou : un modele Turbo n'a pas besoin de >12 steps (20-50 degrade
    # le rendu et ralentit inutilement).
    if t == "turbo":
        steps = min(steps, 12)
    if not negatif:
        negatif = DEFAULT_NEGATIF
    prompt_final = preparer_prompt(prompt, traduire=traduire, reference_desc=reference_desc)

    kontext_actif = ref_ok and t == "flux" and "kontext" in modele.name.lower()

    # Cible 4K : base 16:9 adaptee au modele puis upscale x4 -> 3840x2160.
    if resolution == "4k" and out_final.suffix.lower() == ".png":
        if t in ("sdxl", "flux"):
            largeur, hauteur = 1344, 768
        else:
            largeur, hauteur = 1024, 576
        if kontext_actif:
            # Chaine en 2 passes : base 16:9 (l'upscale apporte le detail).
            largeur, hauteur = 1280, 720
        out = out_final.with_name(out_final.stem + "_base.png")

    def _cmd_flux(modele_p: Path, prompt_p: str, sortie_p: Path,
                  reference_p: str = "", guidance_p: float = 0.0) -> list[str]:
        cmd = [str(_exe())]
        if reference_p:
            cmd += ["-r", reference_p]
        cmd += [
            "--diffusion-model", str(modele_p),
            "-p", prompt_p,
            "--width", str(max(256, min(largeur, 2048))),
            "--height", str(max(256, min(hauteur, 2048))),
            "--steps", str(max(2, min(steps, 60))),
            "-o", str(sortie_p),
        ]
        # Encodeurs separes (t5xxl, clip_l, ae) cherches dans models/.
        d = _dir() / "models"
        for flag, motifs in (("--t5xxl", ("t5xxl",)),
                             ("--clip_l", ("clip_l",)),
                             ("--vae", ("ae.",))):
            for f in sorted(d.iterdir()):
                if any(mot in f.name.lower() for mot in motifs) and f.is_file():
                    cmd += [flag, str(f)]
                    break
        # schnell/kontext : guidance distillée -> cfg-scale 1.0
        cmd += ["--sampling-method", "euler", "--cfg-scale", "1.0"]
        if guidance_p > 0:
            cmd += ["--guidance", str(guidance_p)]
        if negatif:
            cmd += ["--negative-prompt", negatif]
        if graine >= 0:
            cmd += ["--seed", str(graine)]
        return cmd

    kontext_chain = False
    cmds: list[list[str]] = []
    if t == "flux":
        if kontext_actif:
            # Fidelite au sujet reel : chaine Kontext en 2 passes —
            # 1) le sujet de la reference est preserve, la scene s'introduit ;
            # 2) la scene se finalise, le sujet reste identique (derive minime).
            if mode == "edit":
                # img2img : l'image de reference EST le sujet a modifier.
                p1 = f"Edit this image: {prompt_final}"
                p2 = f"Refine the edit, keep everything else identical: {prompt_final}"
            else:
                desc = (reference_desc or "").strip()[:300]
                if desc:
                    p1 = (f"the same subject as in the reference image: {desc}, "
                          f"{prompt_final}")
                    p2 = (f"keep the subject exactly identical to the reference: "
                          f"{desc}, {prompt_final}")
                else:
                    p1 = f"the same subject as in the reference image, {prompt_final}"
                    p2 = (f"keep the subject exactly identical to the reference image, "
                          f"{prompt_final}")
            tmp1 = out.with_name(out.stem + "_k1.png")
            cmds = [
                _cmd_flux(modele, p1, tmp1, str(Path(reference).resolve()), 3.5),
                _cmd_flux(modele, p2, out, str(tmp1), 3.5),
            ]
            kontext_chain = True
        else:
            cmds = [_cmd_flux(modele, prompt_final, out)]
    else:
        cmd = [
            str(_exe()),
            "-m", str(modele),
            "-p", prompt_final,
            "--width", str(max(256, min(largeur, 2048))),
            "--height", str(max(256, min(hauteur, 2048))),
            "--steps", str(max(2, min(steps, 60))),
            "-o", str(out),
        ]
        cmds = [cmd]
    hires = False
    if t == "turbo":
        cmds[0] += ["--cfg-scale", "1.0"]
    elif t != "flux":
        # SDXL/SD1.5 photorealiste : dpm++2m karras + hires fix (2e passe
        # haute resolution qui ajoute le vrai detail « comme devant soi »).
        cmds[0] += ["--sampling-method", "dpm++2m", "--scheduler", "karras",
                    "--cfg-scale", "5.5", "--vae-tiling"]
        if t == "sdxl":
            cmds[0] += ["--hires", "--hires-scale", "1.5",
                        "--hires-denoising-strength", "0.4",
                        "--hires-steps", "10"]
            hires = True
    if not negatif and t != "flux":
        cmds[0] += ["--negative-prompt", DEFAULT_NEGATIF]
    if graine >= 0 and t != "flux":
        cmds[0] += ["--seed", str(graine)]

    # VRAM : on decharge le LLM avant la generation (les 48 Go de RAM restent
    # dispo pour l'offload CPU des gros modeles FLUX).
    _liberer_vram()

    # Prechauffage cache : lecture sequentielle des modeles (mmap de sd-cli
    # est lente a froid sur QLC ; le cache Windows rend les faults instantanees).
    fichiers_chauds = [str(modele)]
    if t == "flux":
        d_models = _dir() / "models"
        for flag, motifs in (("--t5xxl", ("t5xxl",)), ("--clip_l", ("clip_l",)), ("--vae", ("ae.",))):
            for f in sorted(d_models.iterdir()):
                if any(mot in f.name.lower() for mot in motifs) and f.is_file():
                    fichiers_chauds.append(str(f))
                    break
    if kontext_chain:
        fichiers_chauds.append(str(Path(reference).resolve()))
    _prechauffer_cache(fichiers_chauds)

    def _run(c: list[str]):
        return subprocess.run(
            c,
            capture_output=True,
            text=True,
            timeout=1500,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    proc = None
    if kontext_chain:
        tmp1 = Path(cmds[0][cmds[0].index("-o") + 1])
        try:
            p1_proc = _run(cmds[0])
        except subprocess.TimeoutExpired:
            return "Erreur: generation trop longue (>25 min)"
        if p1_proc.returncode != 0 or not tmp1.exists():
            # Passe 1 impossible : repli schnell sans reference.
            try:
                proc = _run(_cmd_flux(modele, prompt_final, out))
            except subprocess.TimeoutExpired:
                return "Erreur: generation trop longue (>25 min)"
        else:
            try:
                proc = _run(cmds[1])
            except subprocess.TimeoutExpired:
                proc = None
            if proc is None or proc.returncode != 0 or not out.exists():
                # Passe 2 ratee : on livre la passe 1 (sujet fidele).
                shutil.copy2(tmp1, out)
                proc = p1_proc
            tmp1.unlink(missing_ok=True)
    else:
        try:
            proc = _run(cmds[0])
        except subprocess.TimeoutExpired:
            return "Erreur: generation trop longue (>25 min)"
        if (proc.returncode != 0 or not out.exists()) and hires:
            # Repli : meme commande sans la passe hires (si OOM/erreur haute res).
            retires = ("--hires", "--hires-scale", "1.5",
                       "--hires-denoising-strength", "0.4", "--hires-steps", "10")
            cmd2 = [a for a in cmds[0] if a not in retires]
            try:
                proc = _run(cmd2)
            except subprocess.TimeoutExpired:
                return "Erreur: generation trop longue (>25 min)"
    if proc.returncode != 0 or not out.exists():
        err = (proc.stderr or proc.stdout or "")[-800:]
        return f"Erreur generation image (code {proc.returncode}): {err}"

    # Upscale 4K : Real-ESRGAN x4 (sinon repli Lanczos vers 3840x2160).
    if resolution == "4k" and out != out_final:
        if _upscale_4k(out, out_final) or _resize_lanczos(out, out_final, 3840, 2160):
            out.unlink(missing_ok=True)
            out = out_final

    try:
        from PIL import Image

        with Image.open(out) as im:
            dims = f"{im.width}x{im.height}"
    except Exception:
        dims = f"{largeur}x{hauteur}"
    ko = out.stat().st_size // 1024
    return (
        f"Image generee: {out} ({ko} Ko) "
        f"[{st['type']}, {steps} steps, {dims}]"
    )
