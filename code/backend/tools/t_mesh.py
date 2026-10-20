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
from backend.tools.common import (_OPENCAD_PATHS, _find_exe,
    _find_vision_model)


_RACINE = os.environ.get("LOCALAI_ROOT", r"E:\ia")
_TSR_PYTHON = os.environ.get("LOCALAI_VENV_PYTHON", str(Path(_RACINE) / ".venv" / "Scripts" / "python.exe"))
_TSR_RUN = str(Path(_RACINE) / "tools" / "TripoSR" / "run.py")
_STL_VIEW_ANGLES = [("face", 68, 0), ("profil", 68, 90), ("dos", 68, 180), ("iso", 68, 315)]



def _load_trimesh(mesh_path: str):
    import trimesh

    return trimesh.load(str(mesh_path), force="mesh")



def _finish_textured_mesh(baked_obj, tmp_out, out_stl, height_mm):
    """Finalise un mesh texture (bake TripoSR) : OBJ conserve (UV gardees) +
    PNG renommee + STL imprimable en prime."""
    import numpy as np
    import trimesh

    tex_src = sorted(Path(tmp_out).rglob("texture.png"))
    if not tex_src:
        return "Erreur bake: texture.png absente"
    stem = str(Path(out_stl).with_suffix(""))
    out_obj = stem + "_tex.obj"
    out_tex = stem + "_tex.png"
    mesh = trimesh.load(str(baked_obj), force="mesh")
    extents = np.asarray(mesh.extents, dtype=float)
    factor = float(height_mm) / max(float(extents.max()), 1e-6)
    mesh.apply_scale(factor)
    mesh.apply_transform(trimesh.transformations.rotation_matrix(-np.pi / 2.0, [0, 1, 0]))
    bounds = np.asarray(mesh.bounds, dtype=float)
    cx = -(bounds[0][0] + bounds[1][0]) / 2.0
    cy = -(bounds[0][1] + bounds[1][1]) / 2.0
    mesh.apply_translation([cx, cy, -bounds[0][2]])
    mesh.export(out_obj)
    shutil.copyfile(str(tex_src[0]), out_tex)
    e = np.asarray(mesh.extents, dtype=float)
    return (
        f"Mesh TEXTURE genere: {out_obj} + {out_tex}\n"
        f"Triangles: {len(mesh.faces)} | "
        f"Dimensions (mm): X={e[0]:.1f} Y={e[1]:.1f} Z={e[2]:.1f}\n"
        "Rendu conseille : blender_render(obj, moteur=cycles) "
        "(texture auto-detectee)."
    )



def _hunyuan21_present() -> bool:
    """Vrai si les poids Hunyuan3D-2.1 (DiT+VAE) sont en cache HF."""
    import glob

    base = os.environ.get("HF_HOME", os.path.expanduser("~/.cache/huggingface"))
    motif = os.path.join(
        base, "hub", "models--tencent--Hunyuan3D-2.1", "snapshots", "*",
        "hunyuan3d-dit-v2-1", "model.fp16.ckpt",
    )
    return bool(glob.glob(motif))


def _generer_mesh_hunyuan(image_path: str, out_stl: str, height_mm: float,
                          variante: str = "mini") -> str:
    """Image -> mesh haute qualite via Hunyuan3D (hy3dgen).

    variante : "mini" (rapide, ~7 Go) ou "2.1" (qualite maximale, PBR).
    Sortie : STL geometry, pose Z=0, centre XY, echelle height_mm.
    """
    import numpy as np
    import trimesh

    try:
        from hy3dgen.shapegen import (
            FaceReducer,
            FloaterRemover,
            DegenerateFaceRemover,
            Hunyuan3DDiTFlowMatchingPipeline,
        )
    except Exception as exc:
        return (
            f"Hunyuan3D indisponible ({exc}) : repli TripoSR au prochain essai."
        )

    try:
        from backend.core.sdgen import _liberer_vram

        _liberer_vram()
    except Exception:
        pass

    workdir = Path(out_stl).parent
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        if variante == "2.1":
            import sys as _sys

            repo = os.environ.get(
                "LOCALAI_HUNYUAN21_DIR", r"E:\ia\tools\Hunyuan3D-2.1\hy3dshape"
            )
            if repo and repo not in _sys.path:
                _sys.path.insert(0, repo)
            from hy3dshape.pipelines import (
                Hunyuan3DDiTFlowMatchingPipeline as Pipeline21,
            )

            pipe = Pipeline21.from_pretrained(
                "tencent/Hunyuan3D-2.1", use_safetensors=False
            )
        else:
            pipe = Hunyuan3DDiTFlowMatchingPipeline.from_pretrained(
                "tencent/Hunyuan3D-2mini", subfolder="hunyuan3d-dit-v2-mini"
            )
        mesh_uni = pipe(image=image_path)[0]
        # Nettoyage : floaters, faces degeneres (toutes variantes).
        for outil in (FloaterRemover(), DegenerateFaceRemover()):
            try:
                mesh_uni = outil(mesh_uni)
            except Exception:
                continue
        if variante != "2.1":
            try:
                mesh_uni = FaceReducer()(mesh_uni)
            except Exception:
                pass
        mesh = trimesh.Trimesh(
            vertices=np.asarray(mesh_uni.vertices, dtype=float),
            faces=np.asarray(mesh_uni.faces, dtype=np.int64),
        )
        trimesh.repair.fix_normals(mesh)
        trimesh.repair.fix_winding(mesh)
        try:
            trimesh.repair.fill_holes(mesh)
        except Exception:
            pass
        # Echelle en mm AVANT le remesh : le pitch voxel devient physique.
        extents = np.asarray(mesh.extents, dtype=float)
        factor = float(height_mm) / max(float(extents.max()), 1e-6)
        mesh.apply_scale(factor)
        # Hunyuan sort Y-up : bascule vers Z-up (notre monde Blender/STL).
        mesh.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2.0, [1, 0, 0]))
        if not mesh.is_watertight:
            try:
                pitch = max(0.35, float(height_mm) / 140.0)
                vox = mesh.copy().voxelized(pitch=pitch).fill()
                remesh = vox.marching_cubes
                if remesh is not None and len(remesh.faces) > 1000:
                    # marching_cubes sort en indices de voxels : retour en mm.
                    remesh.apply_scale(pitch)
                    mesh = remesh
            except Exception:
                pass
        # Limite de triangles : Hunyuan 2.1 sort ~1,1 M faces.
        cible = 250_000
        if len(mesh.faces) > cible:
            try:
                dec = mesh.simplify_quadric_decimation(
                    target_reduction=1.0 - (cible / float(len(mesh.faces)))
                )
                if dec is not None and len(dec.faces) > 1000:
                    trimesh.repair.fix_normals(dec)
                    try:
                        trimesh.repair.fill_holes(dec)
                    except Exception:
                        pass
                    if dec.is_watertight:
                        mesh = dec
            except Exception:
                pass
        bounds = np.asarray(mesh.bounds, dtype=float)
        cx = -(bounds[0][0] + bounds[1][0]) / 2.0
        cy = -(bounds[0][1] + bounds[1][1]) / 2.0
        mesh.apply_translation([cx, cy, -bounds[0][2]])
        mesh.export(out_stl)
        check = trimesh.load(out_stl, force="mesh")
        dims = np.asarray(check.extents, dtype=float)
        return (
            f"Mesh HUNYUAN genere (qualite PBR): {out_stl}\n"
            f"Triangles: {len(check.faces)} | Watertight: {check.is_watertight}\n"
            f"Dimensions (mm): X={dims[0]:.1f} Y={dims[1]:.1f} Z={dims[2]:.1f}\n"
            "Pose sur le plan Z=0, centre en XY. Apercu conseille : blender_render."
        )
    except Exception as exc:
        return f"Erreur Hunyuan3D: {exc}"


def tool_generate_mesh_from_image(
    image_path: str, output_stl: str = "", height_mm: float = 80.0, max_faces: int = 0,
    texture: bool = False,
) -> str:
    """Generation organique : photo de reference -> mesh 3D.

    Moteurs (auto ou surcharge via LOCALAI_MESH_ENGINE=triposr|hunyuan) :
    - hunyuan  : Hunyuan3D-2mini (qualite proche Meshy 2.5, PBR, ~6-8 Go VRAM)
    - triposr  : TripoSR (rapide, approximation)
    Le choix par defaut : hunyuan s'il est installe, sinon triposr.
    """
    image_path = str(Path(image_path).expanduser())
    if not Path(image_path).exists():
        return f"Erreur: image introuvable {image_path}"
    out_stl = str(Path(output_stl).expanduser()) if output_stl else str(Path(image_path).with_suffix(".stl"))
    refus = _verifier_acces(image_path) or _verifier_acces(out_stl, ecriture=True)
    if refus:
        return refus

    # Detourage : evite que le fond (mur, sol, decor) soit reconstruit en
    # geometrie et soude au sujet. Repli silencieux sur l'image d'origine.
    try:
        from PIL import Image
        from rembg import remove

        _src = Image.open(image_path).convert("RGBA")
        _cut = remove(_src)
        _fond = Image.new("RGBA", _cut.size, (255, 255, 255, 255))
        _fond.alpha_composite(_cut)
        _cutout = str(Path(out_stl).with_name(Path(out_stl).stem + "_ref.png"))
        _fond.convert("RGB").save(_cutout)
        if Path(_cutout).is_file():
            image_path = _cutout
    except Exception:
        pass

    # --- Choix du moteur ---
    force = os.environ.get("LOCALAI_MESH_ENGINE", "").lower()
    moteur = force
    if moteur not in ("triposr", "hunyuan", "hunyuan21", "meshy"):
        moteur = "triposr"
        try:
            import hy3dgen  # noqa: F401

            # Qualite maximale si 2.1 est installe, sinon mini (rapide).
            moteur = "hunyuan21" if _hunyuan21_present() else "hunyuan"
        except Exception:
            pass
    if moteur == "meshy":
        if _meshy_token():
            return _generer_mesh_meshy(image_path, out_stl, height_mm)
        return "Erreur: moteur meshy demande mais connecteur absent (Reglages > Connexions)."
    if moteur == "hunyuan21":
        return _generer_mesh_hunyuan(image_path, out_stl, height_mm, variante="2.1")
    if moteur == "hunyuan":
        return _generer_mesh_hunyuan(image_path, out_stl, height_mm)

    try:
        from backend.core.autotune import get_param

        faces_auto = int(get_param("mesh_max_faces", 30000))
    except Exception:
        faces_auto = 30000
    if not max_faces:
        max_faces = faces_auto
    tsr_py = os.environ.get("LOCALAI_TSR_PYTHON", _TSR_PYTHON)
    tsr_run = os.environ.get("LOCALAI_TSR_RUN", _TSR_RUN)
    if not Path(tsr_py).exists() or not Path(tsr_run).exists():
        return (
            "TripoSR n'est pas installe sur cette machine (attendu : "
            + str(Path(_TSR_RUN).parent) + "). "
            "La generation organique est indisponible : restez en modelisation parametrique."
        )
    workdir = Path(out_stl).parent
    workdir.mkdir(parents=True, exist_ok=True)
    tmp_out = workdir / f"_tsr_{Path(out_stl).stem}"
    shutil.rmtree(tmp_out, ignore_errors=True)
    cmd = [
        tsr_py, tsr_run, image_path,
        "--output-dir", str(tmp_out),
        "--model-save-format", "obj",
    ]
    if texture:
        cmd += ["--bake-texture", "--texture-resolution", "1024"]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=1800,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    objs = sorted(Path(tmp_out).rglob("*.obj")) if Path(tmp_out).exists() else []
    if not objs:
        return f"Erreur TripoSR (code {proc.returncode}):\n{(proc.stderr or proc.stdout)[:1500]}"
    if texture:
        return _finish_textured_mesh(objs[0], tmp_out, out_stl, height_mm)
    try:
        import numpy as np
        import trimesh

        mesh = trimesh.load(str(objs[0]), force="mesh")
        try:
            import fast_simplification

            cible = max(int(max_faces), 5000)
            if len(mesh.faces) > cible:
                verts, faces = fast_simplification.simplify(
                    mesh.vertices.astype(np.float32),
                    mesh.faces.astype(np.int64),
                    target_reduction=1.0 - (cible / float(len(mesh.faces))),
                )
                mesh = trimesh.Trimesh(vertices=verts, faces=faces)
                mesh.merge_vertices()
                mesh.update_faces(mesh.unique_faces())
                mesh.update_faces(mesh.nondegenerate_faces())
                trimesh.repair.fix_normals(mesh)
                trimesh.repair.fix_winding(mesh)
                try:
                    trimesh.repair.fill_holes(mesh)
                except Exception:
                    pass
        except Exception:
            pass
        extents = np.asarray(mesh.extents, dtype=float)
        factor = float(height_mm) / max(float(extents.max()), 1e-6)
        mesh.apply_scale(factor)
        # TripoSR sort la hauteur sur X ; notre monde (Blender/STL/impression)
        # est en Z-up : on bascule X vers Z.
        mesh.apply_transform(trimesh.transformations.rotation_matrix(-np.pi / 2.0, [0, 1, 0]))
        bounds = np.asarray(mesh.bounds, dtype=float)
        cx = -(bounds[0][0] + bounds[1][0]) / 2.0
        cy = -(bounds[0][1] + bounds[1][1]) / 2.0
        mesh.apply_translation([cx, cy, -bounds[0][2]])
        mesh.export(out_stl)
    except Exception as exc:
        return f"Erreur conversion mesh: {exc}"
    finally:
        shutil.rmtree(tmp_out, ignore_errors=True)
    e = np.asarray(mesh.extents, dtype=float)
    return (
        f"Mesh genere depuis l'image: {out_stl}\n"
        f"Triangles: {len(mesh.faces)} | Watertight: {mesh.is_watertight}\n"
        f"Dimensions (mm): X={e[0]:.1f} Y={e[1]:.1f} Z={e[2]:.1f}\n"
        "Le mesh est pose sur le plan Z=0, centre en XY."
    )



def tool_mesh_info(mesh_path: str) -> str:
    mesh_path = str(Path(mesh_path).expanduser())
    if not Path(mesh_path).exists():
        return f"Erreur: fichier introuvable {mesh_path}"
    import numpy as np

    m = _load_trimesh(mesh_path)
    e = np.asarray(m.extents, dtype=float)
    b = np.asarray(m.bounds, dtype=float)
    return (
        f"Mesh: {mesh_path}\n"
        f"Triangles: {len(m.faces)} | Watertight: {m.is_watertight}\n"
        f"Dimensions (mm): X={e[0]:.1f} Y={e[1]:.1f} Z={e[2]:.1f}\n"
        f"Z min/max: {b[0][2]:.1f} / {b[1][2]:.1f}\n"
        f"Volume: {abs(float(m.volume)) / 1000.0:.1f} cm3"
    )



def tool_repair_mesh(mesh_path: str) -> str:
    mesh_path = str(Path(mesh_path).expanduser())
    if not Path(mesh_path).exists():
        return f"Erreur: fichier introuvable {mesh_path}"
    import trimesh

    m = _load_trimesh(mesh_path)
    avant = f"{len(m.faces)} tri, watertight={m.is_watertight}"
    backup = mesh_path + ".bak"
    shutil.copyfile(mesh_path, backup)
    steps = []
    try:
        trimesh.repair.fix_normals(m)
        steps.append("normales")
    except Exception:
        pass
    try:
        if trimesh.repair.fill_holes(m):
            steps.append("trous")
    except Exception:
        pass
    try:
        trimesh.repair.fix_inversion(m)
        steps.append("inversion")
    except Exception:
        pass
    try:
        trimesh.repair.fix_winding(m)
    except Exception:
        pass
    # Remesh voxel si le maillage reste ouvert (typique des meshes IA) :
    # garantit un STL imprimable (watertight).
    if not m.is_watertight:
        try:
            dims = [float(x) for x in m.extents]
            pitch = max(0.8, max(dims) / 180.0)
            vox = m.copy().voxelized(pitch=pitch).fill()
            remesh = vox.marching_cubes
            if remesh is not None and len(remesh.faces) > 1000:
                # marching_cubes sort en indices de voxels : retour en mm.
                remesh.apply_scale(pitch)
                # La decimation peut rouvrir le maillage : on ne la garde que si
                # la surface reste parfaitement fermee.
                final = remesh
                try:
                    dec = remesh.simplify_quadric_decimation(target_reduction=0.35)
                    if dec is not None and dec.is_watertight and len(dec.faces) > 1000:
                        final = dec
                except Exception:
                    pass
                m = final
                steps.append(f"remesh voxel {pitch:.1f}mm")
        except Exception:
            pass
    m.export(mesh_path)
    apres = f"{len(m.faces)} tri, watertight={m.is_watertight}"
    return f"Reparation OK ({', '.join(steps) or 'rien a corriger'}): avant={avant} -> apres={apres}. Sauvegarde: {backup}"



def tool_mesh_boolean(op: str, mesh_a: str, mesh_b: str, output_path: str = "") -> str:
    op = (op or "").lower()
    if op not in {"union", "subtract", "intersect"}:
        return "Erreur: operation doit etre union, subtract ou intersect"
    a_p, b_p = str(Path(mesh_a).expanduser()), str(Path(mesh_b).expanduser())
    for p in (a_p, b_p):
        if not Path(p).exists():
            return f"Erreur: fichier introuvable {p}"
    out = str(Path(output_path).expanduser()) if output_path else a_p.replace(".stl", "_fusion.stl")
    refus = _verifier_acces(out, ecriture=True)
    if refus:
        return refus
    try:
        import trimesh

        a = _load_trimesh(a_p)
        b = _load_trimesh(b_p)
        fn = getattr(trimesh.boolean, op)
        res = fn([a, b], engine="manifold")
        res.export(out)
    except Exception as exc:
        return f"Erreur booleenne {op}: {exc}"
    return (
        f"Fusion {op} OK: {out}\nTriangles: {len(res.faces)} | Watertight: {res.is_watertight}\n"
        "Verifiez ensuite avec render_stl_views + validate_mesh."
    )



def _software_render_view(stl_path: str, png_path: str, rx: float = 68.0, rz: float = 0.0) -> bool:
    """Rendu logiciel (sans OpenGL) d'une vue d'un STL : z-buffer + ombrage plat."""
    try:
        import numpy as np
        import trimesh
        from PIL import Image

        m = _load_trimesh(stl_path)
        if len(m.faces) > 15000:
            try:
                import fast_simplification

                verts, faces = fast_simplification.simplify(
                    m.vertices.astype(np.float32),
                    m.faces.astype(np.int64),
                    target_reduction=1.0 - 14000.0 / max(len(m.faces), 1),
                )
                m = trimesh.Trimesh(vertices=np.asarray(verts), faces=np.asarray(faces))
            except Exception:
                pass
        v = np.asarray(m.vertices, dtype=float)
        f = np.asarray(m.faces, dtype=int)
        ax, az = np.radians(rx), np.radians(rz)
        Rz = np.array([[np.cos(az), -np.sin(az), 0], [np.sin(az), np.cos(az), 0], [0, 0, 1]])
        Rx = np.array([[1, 0, 0], [0, np.cos(ax), -np.sin(ax)], [0, np.sin(ax), np.cos(ax)]])
        v = v @ (Rz @ Rx).T
        lo, hi = v.min(axis=0), v.max(axis=0)
        centre = (lo + hi) / 2.0
        span = max(hi[0] - lo[0], hi[2] - lo[2]) * 1.6 or 1.0
        W, H = 600, 450
        echelle = min(W / span, H / (span * 0.75))
        sx = (v[:, 0] - centre[0]) * echelle + W / 2.0
        sy = H / 2.0 - (v[:, 2] - centre[2]) * echelle
        profondeur = -v[:, 1]
        tris = f
        normales = np.asarray(m.face_normals) @ (Rz @ Rx).T
        lum = np.array([0.35, -0.5, 0.79])
        lum = lum / np.linalg.norm(lum)
        intensite = np.clip(np.abs(normales @ lum), 0.15, 1.0)
        base = np.array([168, 172, 180], dtype=float)
        img = np.full((H, W, 3), 245, dtype=np.float64)
        zbuf = np.full((H, W), -1e18)
        for idx in range(len(tris)):
            i0, i1, i2 = tris[idx]
            x0, x1, x2 = sx[i0], sx[i1], sx[i2]
            y0, y1, y2 = sy[i0], sy[i1], sy[i2]
            xmin, xmax = int(max(0, min(x0, x1, x2))), int(min(W - 1, max(x0, x1, x2)) + 1)
            ymin, ymax = int(max(0, min(y0, y1, y2))), int(min(H - 1, max(y0, y1, y2)) + 1)
            # le slicing numpy tronque silencieusement hors bornes alors que
            # arange() garde sa taille -> desaccord de formes. On clamp avant.
            xmax = min(xmax, W - 1)
            ymax = min(ymax, H - 1)
            if xmin > xmax or ymin > ymax:
                continue
            det = (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)
            if abs(det) < 1e-9:
                continue
            xs = np.arange(xmin, xmax + 1) + 0.5
            ys = np.arange(ymin, ymax + 1) + 0.5
            gx, gy = np.meshgrid(xs, ys)
            w1 = ((gx - x0) * (y2 - y0) - (gy - y0) * (x2 - x0)) / det
            w2 = ((gy - y0) * (x1 - x0) - (gx - x0) * (y1 - y0)) / det
            w0 = 1.0 - w1 - w2
            masque = (w0 >= 0) & (w1 >= 0) & (w2 >= 0)
            if not masque.any():
                continue
            pz = w0 * profondeur[i0] + w1 * profondeur[i1] + w2 * profondeur[i2]
            sous_z = zbuf[ymin:ymax + 1, xmin:xmax + 1]
            meilleur = masque & (pz > sous_z)
            if not meilleur.any():
                continue
            sous_z[meilleur] = pz[meilleur]
            coul = base * intensite[idx]
            zone = img[ymin:ymax + 1, xmin:xmax + 1]
            zone[meilleur] = coul
        Image.fromarray(img.astype(np.uint8)).save(png_path)
        return True
    except Exception:
        try:
            import traceback

            traceback.print_exc()
        except Exception:
            pass
        return False



def _repair_volume(m):
    """Repare un mesh; si non etanche, le redevient solide par voxelisation."""
    import trimesh

    try:
        m.merge_vertices()
        m.update_faces(m.unique_faces())
        m.update_faces(m.nondegenerate_faces())
        trimesh.repair.fix_normals(m)
        trimesh.repair.fix_winding(m)
        try:
            trimesh.repair.fill_holes(m)
        except Exception:
            pass
    except Exception:
        pass
    if m.is_watertight and abs(float(m.volume)) > 1e-6:
        return m
    pitch = float(max(m.extents)) / 72.0 or 1.0
    surf = m.voxelized(pitch).fill().marching_cubes
    surf.merge_vertices()
    # marching_cubes sort en indices de voxels : retour en unites d'origine.
    surf.apply_scale(pitch)
    return surf



def tool_fuse_meshes(mesh_a: str, mesh_b: str, output_path: str = "") -> str:
    """Fusion robuste de 2 meshes : reparation (voxelisation si besoin) puis union
    manifold. Repli final : coques concatenees (suffisant en impression FDM)."""
    a_p, b_p = str(Path(mesh_a).expanduser()), str(Path(mesh_b).expanduser())
    for p in (a_p, b_p):
        if not Path(p).exists():
            return f"Erreur: fichier introuvable {p}"
    out = str(Path(output_path).expanduser()) if output_path else a_p.replace(".stl", "_fusion.stl")
    refus = _verifier_acces(out, ecriture=True)
    if refus:
        return refus
    try:
        import numpy as np
        import trimesh

        a = _repair_volume(_load_trimesh(a_p))
        b = _repair_volume(_load_trimesh(b_p))
        try:
            res = trimesh.boolean.union([a, b], engine="manifold")
            methode = "union booleenne"
        except Exception:
            res = trimesh.util.concatenate([a, b])
            methode = "coques concatenes (chevauchement, imprimable en FDM)"
        res.export(out)
    except Exception as exc:
        return f"Erreur fusion: {exc}"
    e = np.asarray(res.extents, dtype=float)
    return (
        f"Fusion OK ({methode}): {out}\n"
        f"Triangles: {len(res.faces)} | Watertight: {res.is_watertight}\n"
        f"Dimensions (mm): X={e[0]:.1f} Y={e[1]:.1f} Z={e[2]:.1f}"
    )



def tool_render_stl_views(stl_path: str) -> str:
    """Rend 4 vues PNG (face/profil/dos/iso) d'un STL pour inspection visuelle."""
    stl_path = str(Path(stl_path).expanduser())
    refus = _verifier_acces(stl_path, ecriture=True)
    if refus:
        return refus
    if not Path(stl_path).exists():
        return f"Erreur: STL introuvable {stl_path}"
    prefix = str(Path(stl_path).with_suffix(""))
    openscad = _find_exe(_OPENCAD_PATHS)
    if not openscad:
        rendus = []
        for nom, rx, rz in _STL_VIEW_ANGLES:
            png = f"{prefix}_{nom}.png"
            if _software_render_view(stl_path, png, rx=rx, rz=rz):
                rendus.append(f"{png} (rendu logiciel)")
        if rendus:
            return (
                "Vues generees (rendu logiciel, OpenSCAD non installe):\n"
                + "\n".join(rendus)
            )
        return "OpenSCAD introuvable et rendu logiciel impossible."
    tmp_scad = prefix + "_views.scad"
    safe_import = stl_path.replace("\\", "/")
    Path(tmp_scad).write_text(f'import("{safe_import}");', encoding="utf-8")
    rendus = []
    try:
        for nom, rx, rz in _STL_VIEW_ANGLES:
            png = f"{prefix}_{nom}.png"
            cmd = [
                openscad, "-o", png,
                "--render",
                "--imgsize", "600,450",
                "--camera", f"0,0,0,{rx},0,{rz},0",
                "--autocenter", "--viewall",
                tmp_scad,
            ]
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if Path(png).exists() and Path(png).stat().st_size > 2000:
                rendus.append(f"{png} ({Path(png).stat().st_size // 1024} Ko)")
    finally:
        try:
            Path(tmp_scad).unlink()
        except OSError:
            pass
    if len(rendus) < 2:
        rendus = []
        for nom, rx, rz in _STL_VIEW_ANGLES:
            png = f"{prefix}_{nom}.png"
            if _software_render_view(stl_path, png, rx=rx, rz=rz):
                rendus.append(f"{png} (rendu logiciel)")
        if rendus:
            return "Vues generees (rendu logiciel, OpenSCAD indisponible):\n" + "\n".join(rendus)
        return f"Erreur rendu des vues: {(proc.stderr or proc.stdout)[:800]}"
    return "Vues generees:\n" + "\n".join(rendus)



def tool_validate_mesh(
    request_text: str, stl_path: str, reference_image: str = ""
) -> str:
    """CONTROLE QUALITE MULTI-VUES : rend 4 vues du STL puis verification croisee par le modele vision."""
    stl_path = str(Path(stl_path).expanduser())
    if not Path(stl_path).exists():
        return f"Erreur: STL introuvable {stl_path}"
    ref = ""
    if reference_image and Path(reference_image).expanduser().exists():
        ref = str(Path(reference_image).expanduser())
    vues_txt = tool_render_stl_views(stl_path)
    prefix = str(Path(stl_path).with_suffix(""))
    images = [f"{prefix}_{nom}.png" for nom, _, _ in _STL_VIEW_ANGLES]
    images = [i for i in images if Path(i).exists()]
    if not images:
        return f"Impossible de rendre les vues.\n{vues_txt}"
    modele = _find_vision_model()
    if not modele:
        return "Aucun modele vision disponible pour valider."
    prompt = (
        "Tu es un controleur qualite d'objets 3D improums, strict et professionnel.\n"
        f"DEMANDE INITIALE DU CLIENT:\n{request_text}\n\n"
        f"On te montre {len(images)} vues du meme objet 3D (face, profil, dos, vue iso)"
        + (", PLUS la photo de reference demandee." if ref else ".")
        + "\nCompare l'objet rendu a la demande (et a la reference si fournie).\n"
        "Liste les ECARTS precis (formes manquantes, mauvaises proportions, elements absents, "
        "parties flottantes impossibles a imprimer, zones trop fines).\n"
        "Sois exige : un objet approximatif doit etre refuse.\n\n"
        "FORMAT DE REPONSE OBLIGATOIRE:\n"
        "VERDICT: OUI ou NON\n"
        "ECARTS: liste a puces (vide si OUI)\n"
        "SCORE RESSEMBLANCE: X/10"
    )
    import httpx

    try:
        base = os.environ.get("LOCALAI_OLLAMA_URL", "http://127.0.0.1:11434")
        imgs_b64 = [
            base64.b64encode(Path(i).read_bytes()).decode("utf-8")
            for i in images + ([ref] if ref else [])
        ]
        rep = httpx.post(
            f"{base}/api/chat",
            json={
                "model": modele,
                "messages": [{"role": "user", "content": prompt, "images": imgs_b64}],
                "stream": False,
                "options": {"temperature": 0.15},
            },
            timeout=600,
        ).json()
    except Exception as exc:
        return f"Erreur modele vision: {exc}"
    texte = rep.get("message", {}).get("content", "")
    verdict = "OUI" if "VERDICT: OUI" in texte.upper() else "NON"
    resultat = (
        f"[Modele vision: {modele}]\n[Vues analysees: {', '.join(Path(i).name for i in images)}"
        + (f" + {Path(ref).name}" if ref else "")
        + "]\n\n"
        + texte.strip()
        + f"\n\nDECISION FINALE: {verdict}"
    )
    return resultat[:5000]


# ---- Moteur Meshy (API cloud, cles via connecteur) ----

def _meshy_token() -> str:
    """Token Meshy depuis app_settings (connecteur_meshy_token)."""
    try:
        from backend.core.database import get_db

        ligne = get_db().query_one(
            "SELECT value FROM app_settings WHERE key='connecteur_meshy_token'"
        )
        return str(ligne.get("value", "")) if ligne else ""
    except Exception:
        return ""


def _generer_mesh_meshy(image_path: str, out_stl: str, height_mm: float,
                        art_style: str = "realistic") -> str:
    """Image -> mesh qualite Meshy via l'API officielle (image-to-3d v2).

    Necessite le connecteur Meshy (app_settings connecteur_meshy_token).
    Polling du task, telechargement du GLB, conversion STL watertight.
    art_style : realistic | sculpture
    """
    import base64

    import httpx
    import numpy as np
    import trimesh

    token = _meshy_token()
    if not token:
        return "Erreur: connecteur Meshy absent (Reglages > Connexions)."
    data = Path(image_path).read_bytes()
    b64 = "data:image/png;base64," + base64.b64encode(data).decode()

    # 1. Creation du task (mode preview = 1/4 des credits, detail correct).
    r = httpx.post(
        "https://api.meshy.ai/openapi/v2/image-to-3d",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "image_url": b64,
            "ai_model": "meshy-5",
            "topology": "quad",
            "target_polycount": 50000,
            "art_style": art_style,
        },
        timeout=60,
    )
    if r.status_code != 200:
        return f"Erreur Meshy ({r.status_code}): {r.text[:400]}"
    task_id = r.json().get("id", "")
    if not task_id:
        return "Erreur Meshy: pas d'id de tache."

    # 2. Polling jusqu'a SUCCEEDED (5 a 10 min).
    import time as _t

    debut = _t.time()
    glb_url = ""
    while _t.time() - debut < 1200:
        _t.sleep(10)
        q = httpx.get(
            f"https://api.meshy.ai/openapi/v2/image-to-3d/{task_id}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        etat = q.json()
        status = etat.get("status", "")
        if status == "SUCCEEDED":
            glb_url = etat.get("model_urls", {}).get("glb", "")
            break
        if status in ("FAILED", "CANCELED", "EXPIRED"):
            return f"Erreur Meshy: tache {status} — {str(etat.get('model_error', ''))[:300]}"
    if not glb_url:
        return "Erreur Meshy: delai depasse (20 min) sans succes."

    # 3. Telechargement GLB -> STL watertight pose.
    glb = httpx.get(glb_url, timeout=300)
    tmp = Path(out_stl).with_suffix(".glb")
    tmp.write_bytes(glb.content)
    m = trimesh.load(str(tmp), force="mesh")
    e0 = np.asarray(m.extents, dtype=float)
    m.apply_scale(float(height_mm) / max(float(e0.max()), 1e-6))
    # Meshy sort Y-up : bascule Z-up.
    m.apply_transform(trimesh.transformations.rotation_matrix(-np.pi / 2.0, [1, 0, 0]))
    bounds = np.asarray(m.bounds, dtype=float)
    m.apply_translation([
        -(bounds[0][0] + bounds[1][0]) / 2.0,
        -(bounds[0][1] + bounds[1][1]) / 2.0,
        -bounds[0][2],
    ])
    m.export(out_stl)
    tmp.unlink(missing_ok=True)
    check = trimesh.load(out_stl, force="mesh")
    e2 = np.asarray(check.extents, dtype=float)
    return (
        f"Mesh MESHY genere (qualite PBR officielle): {out_stl}\n"
        f"Triangles: {len(check.faces)} | Watertight: {check.is_watertight}\n"
        f"Dimensions (mm): X={e2[0]:.1f} Y={e2[1]:.1f} Z={e2[2]:.1f}\n"
        "Pose sur Z=0, centre XY. Apercu : blender_render."
    )
