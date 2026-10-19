# -*- coding: utf-8 -*-
import os
import shutil
import subprocess
from pathlib import Path

from backend.core.security import verifier_acces as _verifier_acces
from backend.tools.common import (_OPENCAD_PATHS, _SLICER_PATHS,
    _find_exe)
from backend.tools.t_image import tool_analyze_image


def tool_render_scad(scad_path: str, stl_path: str = "") -> str:
    scad_path = str(Path(scad_path).expanduser())
    refus = _verifier_acces(scad_path) or _verifier_acces(stl_path or "", ecriture=True)
    if refus:
        return refus
    if not Path(scad_path).exists():
        return f"Erreur: fichier introuvable {scad_path}"
    openscad = _find_exe(_OPENCAD_PATHS)
    if not openscad:
        return (
            "OpenSCAD introuvable. Installez-le depuis openscad.org ou definissez "
            "la variable d'environnement LOCALAI_OPENSCAD vers openscad.exe."
        )
    stl = stl_path or str(Path(scad_path).with_suffix(".stl"))
    proc = subprocess.run(
        [openscad, "-o", stl, scad_path],
        capture_output=True,
        text=True,
        timeout=180,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if proc.returncode != 0 or not Path(stl).exists():
        return f"Erreur OpenSCAD (code {proc.returncode}):\n{(proc.stderr or proc.stdout)[:1500]}"
    size_kb = Path(stl).stat().st_size // 1024
    return f"STL genere: {stl} ({size_kb} Ko)"



def tool_slice_model(stl_path: str, slicer: str = "prusa", out_gcode: str = "") -> str:
    stl_path = str(Path(stl_path).expanduser())
    refus = _verifier_acces(stl_path) or _verifier_acces(out_gcode or "", ecriture=True)
    if refus:
        return refus
    if not Path(stl_path).exists():
        return f"Erreur: STL introuvable {stl_path}"
    slicer = (slicer or "prusa").lower()
    patterns = list(_SLICER_PATHS.get(slicer, _SLICER_PATHS["prusa"]))
    exe = _find_exe(patterns)
    if not exe and os.environ.get("LOCALAI_SLICER"):
        exe = os.environ["LOCALAI_SLICER"]
    if not exe:
        known = ", ".join(k for k in _SLICER_PATHS)
        return (
            f"Slicer '{slicer}' introuvable. Slicers supportes: {known}. "
            "Ou definissez LOCALAI_SLICER vers l'executable console du slicer."
        )
    gcode = out_gcode or str(Path(stl_path).with_suffix(".gcode"))
    if "prusa" in Path(exe).name.lower() or "orca" in Path(exe).name.lower():
        cmd = [exe, "--slice", "--export-gcode", "--output", gcode, stl_path]
    else:
        cmd = [exe, "--slice", "-o", gcode, stl_path]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=600,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if proc.returncode != 0 or not Path(gcode).exists():
        return f"Erreur slicer (code {proc.returncode}):\n{(proc.stderr or proc.stdout)[:1500]}"
    size_mb = round(Path(gcode).stat().st_size / 1_048_576, 1)
    return f"G-code genere: {gcode} ({size_mb} Mo)"



def tool_render_preview(scad_path: str, png_path: str = "") -> str:
    """Genere un apercu PNG d'un script OpenSCAD (pour validation visuelle)."""
    scad_path = str(Path(scad_path).expanduser())
    if not Path(scad_path).exists():
        return f"Erreur: fichier introuvable {scad_path}"
    openscad = _find_exe(_OPENCAD_PATHS)
    if not openscad:
        return "OpenSCAD introuvable (voir outil render_scad)."
    png = png_path or str(Path(scad_path).with_suffix(".png"))
    proc = subprocess.run(
        [
            openscad, "-o", png,
            "--render",
            "--imgsize", "600,450",
            "--autocenter", "--viewall",
            "--projection", "p",
            scad_path,
        ],
        capture_output=True,
        text=True,
        timeout=300,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if proc.returncode != 0 or not Path(png).exists():
        return f"Erreur rendu PNG (code {proc.returncode}): {(proc.stderr or proc.stdout)[:800]}"
    return f"Apercu genere: {png}"



def tool_validate_design(request_text: str, scad_path: str) -> str:
    """Controle qualite : rendu PNG puis verification visuelle par le modele vision.

    Retourne un verdict OUI/NON avec les ecarts entre la demande et la conception.
    """
    scad_path = str(Path(scad_path).expanduser())
    if not Path(scad_path).exists():
        return f"Erreur: fichier introuvable {scad_path}"
    preview = tool_render_preview(scad_path)
    if not preview.startswith("Apercu"):
        return f"Validation impossible: {preview}"
    png = preview.replace("Apercu genere: ", "").strip()
    try:
        source = Path(scad_path).read_text(encoding="utf-8", errors="replace")[:1200]
    except Exception:
        source = "(source illisible)"
    question = (
        "Tu es un controleur qualite STRICT pour la conception 3D.\n"
        f"Demande de l'utilisateur : « {request_text[:400]} »\n\n"
        f"Code OpenSCAD de la conception :\n{source}\n\n"
        "Regarde le rendu 3D ci-joint. Sois EXIGEANT : la forme generale doit "
        "vraiment correspondre a la demande (un cube simple n'est PAS un support "
        "telephone, une boite n'est PAS un engrenage...). Reponds EXACTEMENT :\n"
        "VERDICT: OUI ou NON\n"
        "ECARTS: differences concretes entre demande et conception (formes, "
        "elements manquants, proportions)"
    )
    verdict = tool_analyze_image(png, question)
    stl = Path(scad_path).with_suffix(".stl")
    extra = f"\nSTL present: {stl}" if stl.exists() else "\nSTL absent (render_scad non encore execute)"
    return f"{preview}\n{verdict}{extra}"
