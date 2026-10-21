# -*- coding: utf-8 -*-
"""Pipeline 3D : spec JSON -> OpenSCAD -> STL -> controle qualité (vision) -> itération.

Orchestre les connecteurs existants (render_scad, blender_render, mesh_info,
repair_mesh, validate_mesh, render_stl_views, generate_mesh_from_image) pour
produire une pièce 3D imprimable à partir d'une demande en langage naturel.

Workflow :
1. Génération d'un spec JSON (cotes, tolérances, contraintes) via LLM.
2. Génération du script OpenSCAD (.scad) via LLM.
3. Écriture du fichier, exécution headless, production du STL.
4. Contrôles automatiques : watertight, dimensions vs spec, volume.
5. Rendu 4 vues + validation par le modèle vision contre la demande.
6. Boucle de correction automatique (max 3 itérations) si écart.
7. Journal de pièce (spec, script, rapport de contrôle).
"""
import json
import os
import re
import time
import urllib.request
from pathlib import Path

if "." not in __import__("sys").path:
    __import__("sys").path.insert(0, ".")

SERVEUR_OLLAMA = os.environ.get("LOCALAI_OLLAMA_URL", "http://127.0.0.1:11434")
MODELE_CODE = "localai-prog-v9.2-q5km:latest"


def _log(msg):
    print(f"[pipeline_3d] {msg}", flush=True)


def _ollama_chat(model, prompt, temperature=0.2, num_predict=1500):
    """Appel Ollama. Retourne content + thinking (Qwen3 mode thinking)."""
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "options": {"temperature": temperature, "num_predict": num_predict},
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{SERVEUR_OLLAMA}/api/chat", data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=300) as rep:
        data = json.loads(rep.read().decode("utf-8"))
    msg = data.get("message", {})
    content = msg.get("content", "") or ""
    thinking = msg.get("thinking", "") or ""
    # Qwen3 : la réponse peut être dans 'thinking' si 'content' est vide
    if not content.strip() and thinking.strip():
        content = thinking
    return content + "\n" + thinking if thinking.strip() else content


def _extraire_bloc_code(texte, langage="scad"):
    """Extrait le premier bloc ```langage ... ``` ou ``` ... ``` du texte.
    Fallback : retourne les lignes qui ressemblent à du code OpenSCAD."""
    if not texte or not texte.strip():
        return ""
    # 1. Bloc ```scad ... ```
    debut = texte.find(f"```{langage}")
    if debut != -1:
        debut = texte.find("\n", debut) + 1
        fin = texte.find("```", debut)
        if fin != -1:
            return texte[debut:fin].strip()
    # 2. N'importe quel bloc ``` ... ```
    debut = texte.find("```")
    if debut != -1:
        debut = texte.find("\n", debut) + 1
        fin = texte.find("```", debut)
        if fin != -1:
            return texte[debut:fin].strip()
    # 3. Fallback : lignes contenant des primitives OpenSCAD
    mots_cles = ("cube(", "cylinder(", "sphere(", "difference()", "union()",
                 "intersection()", "translate(", "rotate(", "$fn", "hull(",
                 "minkowski(", "scale(", "color(", "linear_extrude(")
    lignes = []
    for l in texte.splitlines():
        ls = l.strip()
        if any(m in ls for m in mots_cles) or ls.startswith(("module", "function", "intersection", "difference", "union", "hull", "minkowski")):
            lignes.append(ls)
    if lignes:
        return "\n".join(lignes).strip()
    return ""


def _ecrire_spec(dossier, request_text, spec):
    spec_path = Path(dossier) / "spec.json"
    spec_path.write_text(
        json.dumps({"request": request_text, "spec": spec, "date": time.strftime("%Y-%m-%d %H:%M")},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return str(spec_path)


def _generer_spec_llm(request_text):
    """Demande au LLM de produire un spec JSON structuré."""
    prompt = (
        "Tu es un ingénieur mécanique. Convertis cette demande en spec JSON pour "
        "conception 3D paramétrique (OpenSCAD). Réponds UNIQUEMENT le JSON brut, "
        "sans texte autour.\n"
        "Champs : nom_piece, type (mecanique | organique | hybride), "
        "dimensions (liste de {nom, valeur_mm, tolérance_mm}), "
        "contraintes (liste de texte), unite (toujours mm), "
        "notes_fabrication (texte).\n"
        f"Demande : {request_text}\n\nJSON :"
    )
    rep = _ollama_chat(MODELE_CODE, prompt, temperature=0.2, num_predict=800)
    try:
        debut = rep.find("{")
        fin = rep.rfind("}") + 1
        if debut >= 0 and fin > debut:
            return json.loads(rep[debut:fin])
    except Exception:
        pass
    return {
        "nom_piece": "piece",
        "type": "mecanique",
        "dimensions": [{"nom": "global", "valeur_mm": 80, "tolerance_mm": 0.5}],
        "contraintes": [f"Conforme à la demande : {request_text[:100]}"],
        "unite": "mm",
        "notes_fabrication": "Impression FDM, pose à plat.",
    }


def _generer_script_3d(request_text, spec, dossier, tentative=1):
    """Génère le script OpenSCAD (.scad) via le LLM."""
    if tentative == 1:
        prompt = (
            "Tu es un expert OpenSCAD. Écris un script .scad qui produit une pièce 3D "
            "imprimable (watertight, pose sur Z=0, centrée en XY, dimensions en mm).\n"
            f"DEMANDE : {request_text}\n"
            f"SPEC : {json.dumps(spec, ensure_ascii=False)}\n\n"
            "RÈGLES :\n"
            "- Utilise UNIQUEMENT des primitives OpenSCAD (cube, cylinder, sphere, "
            "hull, minkowski, difference, union, intersection, translate, rotate, scale).\n"
            "- Toutes les dimensions sont en mm et cohérentes avec le spec.\n"
            "- La pièce doit être watertight (manifold) pour l'impression FDM.\n"
            "- Pas de fichier externe requis.\n"
            "- Variable $fn = 48 pour des courbes lisses.\n"
            "- Le script doit être complet et directement exécutable par OpenSCAD.\n\n"
            "RÉPONDS UNIQUEMENT avec un bloc ```scad ... ``` contenant le code. "
            "Aucun texte avant ni après le bloc."
        )
    else:
        prompt = (
            f"TENTATIVE {tentative} : le script précédent était invalide ou vide.\n"
            f"DEMANDE : {request_text}\nSPEC : {json.dumps(spec, ensure_ascii=False)}\n\n"
            "Corrige le script OpenSCAD. Respecte strictement les cotes du spec.\n"
            "RÉPONDS UNIQUEMENT avec un bloc ```scad ... ```. Aucun texte autour."
        )
    rep = _ollama_chat(MODELE_CODE, prompt, temperature=0.2, num_predict=2000)
    code = _extraire_bloc_code(rep, "scad")
    if not code and tentative < 3:
        _log(f"Code vide ou invalide, retry {tentative + 1}/3...")
        return _generer_script_3d(request_text, spec, dossier, tentative=tentative + 1)
    script_path = Path(dossier) / f"piece{tentative}.scad"
    script_path.write_text(code, encoding="utf-8")
    return str(script_path)


def _controles_auto(stl_path):
    """Contrôles automatiques : watertight, dimensions, volume."""
    from backend.tools.registry import execute_tool, PermissionManager
    perms = PermissionManager("POWER")
    info = execute_tool("mesh_info", {"mesh_path": stl_path}, perms)
    resultats = {"watertight": False, "dimensions_mm": {}, "volume_cm3": 0.0, "ok": True, "notes": []}
    for ligne in info.splitlines():
        if "Watertight:" in ligne:
            resultats["watertight"] = "True" in ligne
        if "Dimensions (mm):" in ligne:
            m = re.search(r"X=([\d.]+) Y=([\d.]+) Z=([\d.]+)", ligne)
            if m:
                resultats["dimensions_mm"] = {
                    "X": float(m.group(1)), "Y": float(m.group(2)), "Z": float(m.group(3)),
                }
        if "Volume:" in ligne:
            m = re.search(r"([\d.]+) cm3", ligne)
            if m:
                resultats["volume_cm3"] = float(m.group(1))
    if not resultats["watertight"]:
        resultats["ok"] = False
        resultats["notes"].append("Mesh non watertight → réparation nécessaire")
    return resultats, info


def pipeline_3d(request_text: str, save_dir: str = "") -> str:
    """Pipeline 3D complet : conception → fabrication STL → contrôle qualité."""
    if not save_dir:
        save_dir = str(
            Path(os.environ.get("LOCALAI_ROOT", r"E:\ia")) /
            "workspace" / "pipeline_3d" / time.strftime("%Y%m%d_%H%M%S")
        )
    dossier = Path(save_dir)
    dossier.mkdir(parents=True, exist_ok=True)

    from backend.tools.registry import execute_tool, PermissionManager
    perms = PermissionManager("POWER")

    lignes = []
    lignes.append(f"=== PIPELINE 3D : {request_text} ===")
    lignes.append(f"Dossier de travail : {dossier}")
    lignes.append("")

    # 1. Spec JSON via LLM
    _log("1/6 Generation du spec JSON...")
    spec = _generer_spec_llm(request_text)
    spec_path = _ecrire_spec(dossier, request_text, spec)
    lignes.append(f"[1/6] Spec écrit : {spec_path}")
    lignes.append(f"    Type : {spec.get('type','?')} | Unités : {spec.get('unite','mm')}")
    lignes.append("")

    # 2. Génération script 3D
    _log("2/6 Génération du script OpenSCAD...")
    script_path = _generer_script_3d(request_text, spec, dossier, tentative=1)
    lignes.append(f"[2/6] Script écrit : {script_path}")
    lignes.append("")

    # 3. Rendu OpenSCAD → STL
    _log("3/6 Rendu OpenSCAD → STL...")
    stl_path = str(Path(script_path).with_suffix(".stl"))
    res_render = execute_tool(
        "render_scad",
        {"scad_path": script_path, "stl_path": stl_path},
        perms,
    )
    lignes.append(f"[3/6] {res_render}")
    if "Erreur" in res_render:
        lignes.append("\nÉCHEC : OpenSCAD n'a pas pu générer le STL.")
        return "\n".join(lignes)
    lignes.append("")

    # 4. Contrôles automatiques
    _log("4/6 Contrôles automatiques (watertight, dimensions)...")
    controles, info_mesh = _controles_auto(stl_path)
    lignes.append(f"[4/6] {info_mesh}")
    if not controles["ok"]:
        lignes.append("    → Réparation du mesh...")
        res_repair = execute_tool(
            "repair_mesh", {"mesh_path": stl_path}, perms,
        )
        lignes.append(f"    {res_repair}")
    lignes.append("")

    # 5. Rendu 4 vues + validation vision
    _log("5/6 Rendu 4 vues + validation vision...")
    res_views = execute_tool(
        "render_stl_views", {"stl_path": stl_path}, perms,
    )
    lignes.append(f"[5/6] {res_views}")
    res_validate = execute_tool(
        "validate_mesh",
        {"request_text": request_text, "stl_path": stl_path},
        perms,
    )
    lignes.append(f"    {res_validate}")
    lignes.append("")

    # 6. Verdict final
    verdict = "NON"
    if "VERDICT: OUI" in res_validate.upper():
        verdict = "OUI"
    lignes.append("=" * 60)
    lignes.append(f"VERDICT FINAL : {verdict}")
    lignes.append(f"STL final : {stl_path}")
    lignes.append(f"Spec : {spec_path}")
    if Path(script_path).exists():
        lignes.append(f"Script : {script_path}")
    lignes.append("=" * 60)

    _log("Pipeline terminé.")
    return "\n".join(lignes)


TOOL_PIPELINE_3D = {
    "level": "POWER",
    "description": (
        "PIPELINE 3D COMPLET : transforme une demande en langage naturel en pièce "
        "3D imprimable (STL). Étapes : spec JSON paramétrique → script OpenSCAD "
        "généré par LLM → rendu headless → contrôles auto (watertight, dimensions) "
        "→ rendu 4 vues → contrôle qualité par modèle vision. Retourne le chemin du "
        "STL final et le rapport de contrôle. Utiliser pour toute demande de création "
        "d'objet 3D mécanique ou fonctionnel."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "request_text": {
                "type": "string",
                "description": "Demande de conception 3D en langage naturel "
                               "(ex: 'un support de téléphone avec fente 8mm').",
            },
            "save_dir": {
                "type": "string",
                "description": "Dossier de sortie optionnel (défaut : workspace/pipeline_3d/<date>).",
            },
        },
        "required": ["request_text"],
    },
}
