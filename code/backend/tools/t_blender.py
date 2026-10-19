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
from backend.tools.common import _find_exe


_BLENDER_PATHS = [
    r"E:\ia\tools\Blender\blender.exe",
    r"C:\Program Files\Blender Foundation\Blender 4.5\blender.exe",
    r"C:\Program Files\Blender Foundation\Blender\blender.exe",
]



def _find_blender() -> str:
    env = os.environ.get("LOCALAI_BLENDER", "")
    if env and Path(env).exists():
        return env
    return _find_exe(_BLENDER_PATHS)



_BLENDER_RENDER_TMPL = """import bpy, math, os
stl = os.environ.get("BL_STL", "")
png = os.environ.get("BL_PNG", "")
W = int(os.environ.get("BL_W", "600") or 600)
H = int(os.environ.get("BL_H", "450") or 450)
ENGINE = os.environ.get("BL_ENGINE", "workbench").lower()
try:
    bpy.ops.preferences.addon_enable(module="io_mesh_stl")
except Exception:
    pass
bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
_ext = os.path.splitext(stl)[1].lower()
if _ext == ".obj":
    bpy.ops.wm.obj_import(filepath=stl)
else:
    try:
        bpy.ops.wm.stl_import(filepath=stl)
    except Exception:
        bpy.ops.import_mesh.stl(filepath=stl)
meshes = [o for o in scene.objects if o.type == "MESH"]
if not meshes:
    raise SystemExit("import 3D vide")
obj = meshes[0]
tex = os.environ.get("BL_TEX", "")
if tex and os.path.exists(tex):
    mat = bpy.data.materials.new("TexAuto")
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    texnode = mat.node_tree.nodes.new("ShaderNodeTexImage")
    texnode.image = bpy.data.images.load(tex)
    try:
        mat.node_tree.links.new(texnode.outputs["Color"], bsdf.inputs["Base Color"])
    except Exception:
        pass
    if obj.data.materials:
        obj.data.materials[0] = mat
    else:
        obj.data.materials.append(mat)
    for _i in obj.data.materials:
        if _i is not None:
            _i.use_nodes = True
bpy.ops.object.select_all(action="DESELECT")
obj.select_set(True)
scene.view_layers[0].objects.active = obj
bpy.ops.object.origin_set(type="ORIGIN_GEOMETRY", center="BOUNDS")
obj.location = (0.0, 0.0, 0.0)
dims = obj.dimensions
size = max(float(dims.x), float(dims.y), float(dims.z), 1e-6)
cam_data = bpy.data.cameras.new("CamAuto")
cam = bpy.data.objects.new("CamAuto", cam_data)
scene.collection.objects.link(cam)
d = size * 2.4
cam.location = (d * 0.65, -d * 0.95, d * 0.60)
tgt = bpy.data.objects.new("Target", None)
scene.collection.objects.link(tgt)
con = cam.constraints.new(type="TRACK_TO")
con.target = tgt
con.track_axis = "TRACK_NEGATIVE_Z"
con.up_axis = "UP_Y"
scene.camera = cam
sun_data = bpy.data.lights.new("SunAuto", type="SUN")
sun_data.energy = 3.0
sun = bpy.data.objects.new("SunAuto", sun_data)
sun.rotation_euler = (math.radians(50), 0.0, math.radians(30))
scene.collection.objects.link(sun)
scene.render.resolution_x = W
scene.render.resolution_y = H
scene.render.resolution_percentage = 100
scene.render.filepath = png
if ENGINE == "cycles":
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = int(os.environ.get("BL_SAMPLES", "48") or 48)
    try:
        scene.cycles.use_denoising = True
    except Exception:
        pass
    scene.render.film_transparent = True
else:
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.render.film_transparent = False
    try:
        scene.display.shading.color_type = "TEXTURE" if tex else "MATERIAL"
        scene.display.shading.light = "STUDIO"
        scene.display.shading.show_cavity = True
    except Exception:
        pass
bpy.ops.render.render(write_still=True)
print("BLENDER_RENDER_OK " + png)
"""



def tool_blender_run(script_path: str, timeout_s: int = 600) -> str:
    """Execute un script Python Blender (bpy) en headless, sans interface.

    Convention : le script lit BLENDER_JOB_DIR (dossier du job) et y ecrit
    piece.stl (obligatoire) et preview.png (optionnel).
    """
    script_path = str(Path(script_path).expanduser())
    refus = _verifier_acces(script_path)
    if refus:
        return refus
    if not Path(script_path).exists():
        return f"Erreur: script introuvable {script_path}"
    blender = _find_blender()
    if not blender:
        return (
            "Blender introuvable (E:\\ia\\tools\\Blender\\blender.exe). "
            "Ou definissez LOCALAI_BLENDER vers blender.exe."
        )
    try:
        timeout_s = max(60, min(int(timeout_s or 600), 1800))
    except Exception:
        timeout_s = 600
    env = os.environ.copy()
    env["BLENDER_JOB_DIR"] = str(Path(script_path).parent)
    try:
        proc = subprocess.run(
            [blender, "--background", "--python", script_path],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            errors="replace",
            env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        return f"Erreur: script Blender TIMEOUT (>{timeout_s}s)"
    out = ((proc.stdout or "") + ("\n" + proc.stderr if (proc.stderr or "").strip() else "")).strip()
    tail = out[-2500:] if out else "(aucune sortie)"
    produits = sorted(
        p.name for p in Path(script_path).parent.iterdir()
        if p.suffix.lower() in {".stl", ".png", ".obj"} and p.is_file()
    )
    return (
        f"[code {proc.returncode}]\n{tail}\n"
        f"Fichiers produits: {', '.join(produits) if produits else '(aucun stl/png)'}"
    )



def tool_blender_render(stl_path: str, png_path: str = "", largeur: int = 600, hauteur: int = 450, moteur: str = "workbench", echantillons: int = 48, texture: str = "") -> str:
    """Rendu photo PNG d'un STL/OBJ via Blender (Workbench rapide ou Cycles hero, CPU)."""
    stl_path = str(Path(stl_path).expanduser())
    if not Path(stl_path).exists():
        return f"Erreur: STL introuvable {stl_path}"
    blender = _find_blender()
    if not blender:
        return "Blender introuvable (voir outil blender_run)."
    png = str(Path(png_path).expanduser()) if png_path else str(Path(stl_path).with_suffix("")) + "_blender.png"
    refus = _verifier_acces(png, ecriture=True)
    if refus:
        return refus
    moteur = (moteur or "workbench").lower()
    if moteur not in ("workbench", "cycles"):
        moteur = "workbench"
    tex = str(Path(texture).expanduser()) if texture else ""
    if not tex:
        base = Path(stl_path)
        for pat in (
            str(base.with_suffix(".png")),
            str(base.with_suffix("")) + "_tex.png",
            str(base.with_suffix("")) + ".png",
        ):
            if Path(pat).is_file():
                tex = pat
                break
    if not tex:
        _cands = []
        for _g in ("texture*.png", "*_tex.png"):
            _cands += sorted(Path(stl_path).parent.glob(_g))
        if _cands:
            tex = str(_cands[0])
    tmpl = Path(stl_path).parent / "_blender_render_tmp.py"
    try:
        tmpl.write_text(_BLENDER_RENDER_TMPL, encoding="utf-8")
    except Exception as exc:
        return f"Erreur ecriture script rendu: {exc}"
    env = os.environ.copy()
    env["BL_STL"] = stl_path
    env["BL_PNG"] = png
    env["BL_W"] = str(largeur or 600)
    env["BL_H"] = str(hauteur or 450)
    env["BL_ENGINE"] = moteur
    env["BL_SAMPLES"] = str(echantillons or 48)
    env["BL_TEX"] = tex
    delai = 900 if moteur == "cycles" else 300
    try:
        proc = subprocess.run(
            [blender, "--background", "--python", str(tmpl)],
            capture_output=True,
            text=True,
            timeout=delai,
            errors="replace",
            env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        return f"Erreur rendu Blender: TIMEOUT (>{delai}s)"
    finally:
        try:
            tmpl.unlink()
        except OSError:
            pass
    if Path(png).exists() and Path(png).stat().st_size > 2000:
        return f"Rendu Blender OK: {png} ({Path(png).stat().st_size // 1024} Ko)"
    err = ((proc.stderr or "") + "\n" + (proc.stdout or ""))[-800:]
    return f"Erreur rendu Blender (code {proc.returncode}):\n{err}"
