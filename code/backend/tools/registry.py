# -*- coding: utf-8 -*-
ALLOWED_LEVELS = ["SAFE", "USER", "POWER", "ADMIN"]
_LEVEL_ORDER = {name: i for i, name in enumerate(ALLOWED_LEVELS)}
MAX_TOOL_ROUNDS = 14

from backend.tools.t_util import tool_calculator, tool_get_datetime
from backend.tools.t_files import (tool_check_code, tool_list_dir,
    tool_read_file, tool_run_command, tool_write_file)
from backend.tools.t_web import (tool_deep_research, tool_web_fetch,
    tool_web_search)
from backend.tools.t_image import (tool_analyze_image, tool_edit_image,
    tool_fetch_image, tool_find_reference_image, tool_generate_image,
    tool_image_search)
from backend.tools.t_cad import (tool_render_preview, tool_render_scad,
    tool_slice_model, tool_validate_design)
from backend.tools.t_mesh import (tool_fuse_meshes,
    tool_generate_mesh_from_image, tool_mesh_boolean, tool_mesh_info,
    tool_repair_mesh, tool_render_stl_views, tool_validate_mesh)
from backend.tools.t_blender import tool_blender_render, tool_blender_run
from backend.tools.t_docs import tool_document_create
from backend.tools.t_extra import (tool_convertir_image, tool_memoire_ajouter,
    tool_notes, tool_presse_papiers, tool_rechercher_fichiers,
    tool_telecharger_fichier)
from backend.tools.t_systeme import (tool_capture_ecran, tool_meteo,
    tool_open_app, tool_system_stats, tool_volume)


class PermissionDenied(Exception):
    pass


def level_rank(level: str) -> int:
    return _LEVEL_ORDER.get(level.upper(), 0)


class PermissionManager:
    def __init__(self, current_level: str):
        self.current = (current_level or "USER").upper()
        if self.current not in _LEVEL_ORDER:
            self.current = "USER"

    def check(self, required: str) -> None:
        if level_rank(self.current) < level_rank(required):
            raise PermissionDenied(
                f"Outil refuse : niveau '{required}' requis, niveau actuel '{self.current}'"
            )



TOOL_DEFINITIONS: dict[str, dict] = {
    "memoire_ajouter": {
        "level": "USER",
        "description": (
            "Ajoute une information DURABLE a ta memoire (preferences, faits, "
            "projets de l'utilisateur). Injectee dans chaque future conversation."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "contenu": {"type": "string", "description": "l'information a retenir (une phrase concise)"},
            },
            "required": ["contenu"],
        },
        "function": lambda args: tool_memoire_ajouter(args["contenu"]),
    },
    "notes": {
        "level": "USER",
        "description": (
            "Notes personnelles persistantes (workspace/notes.md). action: "
            "'ecrire' (ajoute, avec titre optionnel), 'lire' (dernieres notes), 'effacer'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "ecrire | lire | effacer"},
                "texte": {"type": "string", "description": "contenu de la note (pour ecrire)"},
                "titre": {"type": "string", "description": "titre optionnel de la note"},
            },
            "required": ["action"],
        },
        "function": lambda args: tool_notes(
            args["action"], str(args.get("texte", "")), str(args.get("titre", ""))
        ),
    },
    "rechercher_fichiers": {
        "level": "USER",
        "description": (
            "Cherche des fichiers par nom (jokers autorises) dans le workspace "
            "et les documents. Retourne chemin + taille + date."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "motif": {"type": "string", "description": "nom ou motif (*, ?) du fichier"},
                "dossier": {"type": "string", "description": "dossier optionnel (workspace ou documents)"},
            },
            "required": ["motif"],
        },
        "function": lambda args: tool_rechercher_fichiers(
            args["motif"], str(args.get("dossier", ""))
        ),
    },
    "presse_papiers": {
        "level": "USER",
        "description": (
            "Presse-papiers Windows : action 'lire' (retourne le contenu) ou "
            "'ecrire' (y place le texte). Pratique pour copier un resultat."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "lire | ecrire"},
                "texte": {"type": "string", "description": "texte a placer (pour ecrire)"},
            },
            "required": ["action"],
        },
        "function": lambda args: tool_presse_papiers(
            args["action"], str(args.get("texte", ""))
        ),
    },
    "convertir_image": {
        "level": "USER",
        "description": (
            "Redimensionne ou convertit une image locale (PIL) : largeur et/ou "
            "hauteur cible, format de sortie png/jpg/webp. Utile pour adapter "
            "une image generee (miniature, banniere, conversion)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "source": {"type": "string", "description": "chemin de l'image source"},
                "largeur": {"type": "integer", "description": "largeur cible (0 = proportionnel/inchange)"},
                "hauteur": {"type": "integer", "description": "hauteur cible (0 = proportionnel/inchange)"},
                "format_sortie": {"type": "string", "description": "png | jpg | webp (defaut = source)"},
                "destination": {"type": "string", "description": "chemin de sortie optionnel"},
            },
            "required": ["source"],
        },
        "function": lambda args: tool_convertir_image(
            args["source"], int(args.get("largeur", 0)), int(args.get("hauteur", 0)),
            str(args.get("format_sortie", "")), str(args.get("destination", "")),
        ),
    },
    "telecharger_fichier": {
        "level": "USER",
        "description": (
            "Telecharge un fichier depuis une URL (PDF, archive, code, "
            "image...) vers le dossier de travail sandbox. Retourne le "
            "chemin local. Pour les documents recus par lien, avant "
            "read_file ou analyse."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL du fichier"},
                "destination": {"type": "string", "description": "chemin de sortie optionnel (dossier sandbox)"},
            },
            "required": ["url"],
        },
        "function": lambda args: tool_telecharger_fichier(
            args["url"], str(args.get("destination", ""))
        ),
    },
    "calculator": {
        "level": "SAFE",
        "description": "Calcule une expression mathematique (+ - * / ** %)",
        "parameters": {
            "type": "object",
            "properties": {"expression": {"type": "string"}},
            "required": ["expression"],
        },
        "function": lambda args: tool_calculator(args["expression"]),
    },
    "get_datetime": {
        "level": "SAFE",
        "description": "Donne la date et l'heure actuelles",
        "parameters": {"type": "object", "properties": {}},
        "function": lambda args: tool_get_datetime(),
    },
    "generate_image": {
        "level": "POWER",
        "description": (
            "Genere une image 2D photorealiste 4K (FLUX local). A utiliser en "
            "PRIORITE pour toute demande d'image, dessin, illustration, logo ou "
            "affiche. Le prompt peut etre en francais (traduit automatiquement en "
            "anglais). Pour un SUJET REEL connu (avion SR-71, Tour Eiffel, animal "
            "precis...), appelle d'abord find_reference_image puis passe son chemin "
            "en 'reference' pour une fidelite maximale au vrai sujet (FLUX Kontext). "
            "Retourne le chemin du PNG cree."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "description de l'image (FR ou EN)"},
                "save_path": {"type": "string"},
                "negatif": {"type": "string", "description": "choses a exclure (vide = defaut)"},
                "reference": {"type": "string", "description": "chemin d'une image locale de reference du sujet (find_reference_image) - fidelite maximale"},
                "reference_desc": {"type": "string", "description": "description visuelle du sujet de reference (fournie par find_reference_image)"},
                "variantes": {"type": "integer", "description": "nombre d'images a produire (1 a 3). Chacune est notee par le modele vision (note /10 + fidelite) et la meilleure est retournee en tete"},
            },
            "required": ["prompt"],
        },
        "function": lambda args: tool_generate_image(
            args["prompt"],
            args.get("save_path", ""),
            int(args.get("largeur", 0)),
            int(args.get("hauteur", 0)),
            str(args.get("negatif", "")),
            int(args.get("steps", 0)),
            str(args.get("reference", "")),
            str(args.get("reference_desc", "")),
            int(args.get("variantes", 1)),
        ),
    },
    "edit_image": {
        "level": "POWER",
        "description": (
            "Modifie une image EXISTANTE (img2img FLUX Kontext) : recoloration, "
            "changement de ciel/decor, transformation de style, ajout/suppression "
            "d'elements, amelioration. L'image d'entree est conservee, une nouvelle "
            "image modifiee est creee (4K). A utiliser quand l'utilisateur fournit "
            "une image et demande de la modifier/retoucher/transformer."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "image_path": {"type": "string", "description": "chemin de l'image a modifier"},
                "prompt": {"type": "string", "description": "modifications demandees (FR ou EN)"},
                "save_path": {"type": "string"},
            },
            "required": ["image_path", "prompt"],
        },
        "function": lambda args: tool_edit_image(
            args["image_path"], args["prompt"], args.get("save_path", "")
        ),
    },
    "document_create": {
        "level": "POWER",
        "description": (
            "Cree un document professionnel (Word .docx, PDF, ou tableur .xlsx) "
            "a partir d'un contenu en Markdown (titres #, listes -, tableaux "
            "| a | b |). Retourne le chemin du fichier cree."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "titre": {"type": "string", "description": "titre du document"},
                "contenu_md": {"type": "string", "description": "contenu complet en Markdown"},
                "type_doc": {"type": "string", "description": "docx | pdf | xlsx (defaut docx)"},
                "save_path": {"type": "string"},
            },
            "required": ["titre", "contenu_md"],
        },
        "function": lambda args: tool_document_create(
            args["titre"], args["contenu_md"],
            args.get("type_doc", "docx"), args.get("save_path", "")
        ),
    },
    "meteo": {
        "level": "USER",
        "description": "Meteo actuelle + aujourd'hui/demain pour une ville (API Open-Meteo gratuite, sans cle)",
        "parameters": {
            "type": "object",
            "properties": {"ville": {"type": "string", "description": "nom de la ville"}},
            "required": ["ville"],
        },
        "function": lambda args: tool_meteo(args["ville"]),
    },
    "system_stats": {
        "level": "USER",
        "description": "Statistiques du PC : CPU, RAM, disques, GPU (temperature, memoire, utilisation)",
        "parameters": {"type": "object", "properties": {}},
        "function": lambda args: tool_system_stats(),
    },
    "volume": {
        "level": "POWER",
        "description": "Controle le volume systeme : 'up', 'down', 'muet', un nombre (ex '35'), ou vide pour l'etat",
        "parameters": {
            "type": "object",
            "properties": {"action": {"type": "string", "description": "up | down | muet | nombre | vide"}},
            "required": ["action"],
        },
        "function": lambda args: tool_volume(args.get("action", "")),
    },
    "open_app": {
        "level": "POWER",
        "description": "Ouvre une application connue (notepad, calculatrice, chrome, firefox, edge, vlc, explorateur, paint, parametres, powershell...)",
        "parameters": {
            "type": "object",
            "properties": {"nom": {"type": "string", "description": "nom de l'application"}},
            "required": ["nom"],
        },
        "function": lambda args: tool_open_app(args["nom"]),
    },
    "capture_ecran": {
        "level": "POWER",
        "description": "Capture l'ecran et repond a une question dessus avec le modele vision ('c'est quoi cette erreur ?', 'lis ce texte'...)",
        "parameters": {
            "type": "object",
            "properties": {"question": {"type": "string", "description": "question sur le contenu de l'ecran"}},
            "required": ["question"],
        },
        "function": lambda args: tool_capture_ecran(args.get("question", "")),
    },
    "list_dir": {
        "level": "USER",
        "description": "Liste le contenu d'un dossier",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
        "function": lambda args: tool_list_dir(args["path"]),
    },
    "read_file": {
        "level": "USER",
        "description": "Lit le contenu d'un fichier texte",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
        "function": lambda args: tool_read_file(args["path"]),
    },
    "write_file": {
        "level": "POWER",
        "description": "Ecrit du contenu dans un fichier",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
        "function": lambda args: tool_write_file(args["path"], args["content"]),
    },
    "run_command": {
        "level": "POWER",
        "description": "Execute une commande shell et retourne sa sortie",
        "parameters": {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
        "function": lambda args: tool_run_command(args["command"]),
    },
    "web_search": {
        "level": "USER",
        "description": "Recherche sur internet (DuckDuckGo) : titre, URL et extrait des resultats. A utiliser pour toute question d'actualite ou de fait recent.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        "function": lambda args: tool_web_search(args["query"]),
    },
    "deep_research": {
        "level": "USER",
        "description": "Recherche approfondie multi-etapes : genere plusieurs requetes, lit les pages web et produit une synthese avec sources. A utiliser pour les sujets complexes qui meritent une vraie investigation (pas les questions rapides).",
        "parameters": {
            "type": "object",
            "properties": {
                "sujet": {"type": "string"},
                "profondeur": {"type": "integer", "description": "1 a 3 (defaut 2)"},
            },
            "required": ["sujet"],
        },
        "function": lambda args: tool_deep_research(
            args["sujet"], args.get("profondeur", 2)
        ),
    },
    "check_code": {
        "level": "POWER",
        "description": "Agent de controle : valide la syntaxe d'un code (python, javascript, json) et peut l'executer en sandbox 15s pour verifier qu'il fonctionne. A utiliser APRES avoir ecrit du code important, avant de le livrer.",
        "parameters": {
            "type": "object",
            "properties": {
                "code": {"type": "string"},
                "langage": {"type": "string", "description": "python | javascript | json"},
                "executer": {"type": "boolean", "description": "executer en sandbox (python uniquement)"},
            },
            "required": ["code"],
        },
        "function": lambda args: tool_check_code(
            args["code"], args.get("langage", "python"), bool(args.get("executer"))
        ),
    },
    "web_fetch": {
        "level": "USER",
        "description": "Recupere le contenu texte d'une page web a partir de son URL",
        "parameters": {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
        "function": lambda args: tool_web_fetch(args["url"]),
    },
    "render_scad": {
        "level": "POWER",
        "description": "Genere un fichier STL a partir d'un script OpenSCAD (.scad). Workflow 3D : ecrire le .scad avec write_file puis appeler render_scad.",
        "parameters": {
            "type": "object",
            "properties": {
                "scad_path": {"type": "string"},
                "stl_path": {"type": "string"},
            },
            "required": ["scad_path"],
        },
        "function": lambda args: tool_render_scad(args["scad_path"], args.get("stl_path", "")),
    },
    "slice_model": {
        "level": "POWER",
        "description": "Tranche un fichier STL en G-code avec un slicer installe (prusa, orca, cura, bambu)",
        "parameters": {
            "type": "object",
            "properties": {
                "stl_path": {"type": "string"},
                "slicer": {"type": "string"},
                "out_gcode": {"type": "string"},
            },
            "required": ["stl_path"],
        },
        "function": lambda args: tool_slice_model(
            args["stl_path"], args.get("slicer", "prusa"), args.get("out_gcode", "")
        ),
    },
    "image_search": {
        "level": "USER",
        "description": "Recherche d'images de reference sur DuckDuckGo : titres + URL d'images. A utiliser pour voir a quoi ressemble un objet avant de le concevoir en 3D.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        "function": lambda args: tool_image_search(args["query"]),
    },
    "fetch_image": {
        "level": "USER",
        "description": "Telecharge une image depuis son URL et retourne le chemin local (pour analyse par le modele vision)",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "save_path": {"type": "string"},
            },
            "required": ["url"],
        },
        "function": lambda args: tool_fetch_image(args["url"], args.get("save_path", "")),
    },
    "analyze_image": {
        "level": "USER",
        "description": "Analyse une image locale avec un modele vision : description, reconnaissance d'objet, comparaison avec une demande",
        "parameters": {
            "type": "object",
            "properties": {
                "image_path": {"type": "string"},
                "question": {"type": "string"},
            },
            "required": ["image_path", "question"],
        },
        "function": lambda args: tool_analyze_image(args["image_path"], args["question"]),
    },
    "render_preview": {
        "level": "POWER",
        "description": "Genere un apercu PNG d'un script OpenSCAD pour inspection visuelle",
        "parameters": {
            "type": "object",
            "properties": {
                "scad_path": {"type": "string"},
                "png_path": {"type": "string"},
            },
            "required": ["scad_path"],
        },
        "function": lambda args: tool_render_preview(args["scad_path"], args.get("png_path", "")),
    },
    "validate_design": {
        "level": "POWER",
        "description": "CONTROLE QUALITE 3D : rend un apercu PNG du script OpenSCAD puis fait verifier par le modele vision que l'objet correspond a la demande. Retourne VERDICT OUI/NON + ecarts. Obligatoire avant le tranchage.",
        "parameters": {
            "type": "object",
            "properties": {
                "request_text": {"type": "string"},
                "scad_path": {"type": "string"},
            },
            "required": ["request_text", "scad_path"],
        },
        "function": lambda args: tool_validate_design(args["request_text"], args["scad_path"]),
    },
    "find_reference_image": {
        "level": "USER",
        "description": "RECHERCHE VISUELLE INTELLIGENTE : cherche des photos de l'objet demande, telecharge les candidates et fait valider chaque image par le modele vision. Retourne la meilleure reference locale + description geometrique. A utiliser AVANT toute conception 3D.",
        "parameters": {
            "type": "object",
            "properties": {"object_query": {"type": "string"}},
            "required": ["object_query"],
        },
        "function": lambda args: tool_find_reference_image(args["object_query"]),
    },
    "generate_mesh_from_image": {
        "level": "POWER",
        "description": "GENERATION ORGANIQUE PAR IA (TripoSR local) : transforme une photo de reference en mesh 3D imprimable (.stl), echelle a height_mm. Pour les formes organiques impossibles en OpenSCAD : tetes, personnages, sculptures. Compter 1 a 2 minutes. Mettre texture=true pour obtenir en plus la version TEXTUREE (OBJ + PNG, style Meshy) au lieu du mesh uni.",
        "parameters": {
            "type": "object",
            "properties": {
                "image_path": {"type": "string"},
                "output_stl": {"type": "string"},
                "height_mm": {"type": "number"},
                "texture": {"type": "boolean", "description": "true = bake la texture (OBJ+PNG texturés)"},
            },
            "required": ["image_path"],
        },
        "function": lambda args: tool_generate_mesh_from_image(
            args["image_path"], args.get("output_stl", ""), float(args.get("height_mm", 80)),
            0, bool(args.get("texture", False)),
        ),
    },
    "mesh_info": {
        "level": "SAFE",
        "description": "Statistiques d'un fichier 3D (STL/OBJ) : triangles, watertight, dimensions mm, volume cm3, position Z",
        "parameters": {
            "type": "object",
            "properties": {"mesh_path": {"type": "string"}},
            "required": ["mesh_path"],
        },
        "function": lambda args: tool_mesh_info(args["mesh_path"]),
    },
    "repair_mesh": {
        "level": "POWER",
        "description": "Repare un mesh 3D : normales inverses, trous, orientation. A faire sur tout mesh genere par IA avant impression ou fusion",
        "parameters": {
            "type": "object",
            "properties": {"mesh_path": {"type": "string"}},
            "required": ["mesh_path"],
        },
        "function": lambda args: tool_repair_mesh(args["mesh_path"]),
    },
    "mesh_boolean": {
        "level": "POWER",
        "description": "Operation booleenne EXACTE entre 2 meshes (union/subtract/intersect). Exige des meshes watertight. Pour assembler simplement 2 pieces, prefere fuse_meshes qui tolere les maillages imparfaits",
        "parameters": {
            "type": "object",
            "properties": {
                "op": {"type": "string"},
                "mesh_a": {"type": "string"},
                "mesh_b": {"type": "string"},
                "output_path": {"type": "string"},
            },
            "required": ["op", "mesh_a", "mesh_b"],
        },
        "function": lambda args: tool_mesh_boolean(
            args["op"], args["mesh_a"], args["mesh_b"], args.get("output_path", "")
        ),
    },
    "fuse_meshes": {
        "level": "POWER",
        "description": "FUSION ROBUSTE de 2 fichiers STL en un seul : repare automatiquement les maillages defectueux (voxelisation) puis union booleenne. A utiliser pour assembler la forme organique generee par IA avec la piece fonctionnelle OpenSCAD. Les pieces doivent se chevaucher d'au moins 2 mm.",
        "parameters": {
            "type": "object",
            "properties": {
                "mesh_a": {"type": "string"},
                "mesh_b": {"type": "string"},
                "output_path": {"type": "string"},
            },
            "required": ["mesh_a", "mesh_b"],
        },
        "function": lambda args: tool_fuse_meshes(
            args["mesh_a"], args["mesh_b"], args.get("output_path", "")
        ),
    },
    "render_stl_views": {
        "level": "POWER",
        "description": "Rend 4 vues PNG d'un fichier STL (face/profil/dos/iso) pour inspection visuelle par le modele vision",
        "parameters": {
            "type": "object",
            "properties": {"stl_path": {"type": "string"}},
            "required": ["stl_path"],
        },
        "function": lambda args: tool_render_stl_views(args["stl_path"]),
    },
    "validate_mesh": {
        "level": "POWER",
        "description": "CONTROLE QUALITE MULTI-VUES D'UN STL : rend 4 vues puis verification croisee par le modele vision contre la demande (+ reference optionnelle). VERDICT OUI/NON + ecarts + score. Obligatoire avant tranchage pour tout mesh.",
        "parameters": {
            "type": "object",
            "properties": {
                "request_text": {"type": "string"},
                "stl_path": {"type": "string"},
                "reference_image": {"type": "string"},
            },
            "required": ["request_text", "stl_path"],
        },
        "function": lambda args: tool_validate_mesh(
            args["request_text"], args["stl_path"], args.get("reference_image", "")
        ),
    },
    "blender_run": {
        "level": "POWER",
        "description": (
            "Execute un script Python Blender (bpy) en headless, sans interface. "
            "Workflow : ecrire le script avec write_file (import bpy, creer la geometrie "
            "avec les cotes demandes), puis blender_run. Le script DOIT lire "
            "BLENDER_JOB_DIR (variable d'environnement = dossier du job) et y ecrire "
            "piece.stl. Pour un apercu photo, utiliser ensuite blender_render."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "script_path": {"type": "string"},
                "timeout_s": {"type": "integer"},
            },
            "required": ["script_path"],
        },
        "function": lambda args: tool_blender_run(
            args["script_path"], int(args.get("timeout_s", 600))
        ),
    },
    "blender_render": {
        "level": "POWER",
        "description": (
            "Rendu photo PNG d'un fichier STL/OBJ via Blender (eclairage + camera "
            "automatiques, sans GPU). Moteur 'workbench' (rapide, ~30s) ou 'cycles' "
            "(hero, ~5-10 min CPU, fond transparent). Si le STL/OBJ a une texture "
            "(bake TripoSR), elle est appliquee automatiquement (ou via 'texture')."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "stl_path": {"type": "string"},
                "png_path": {"type": "string"},
                "largeur": {"type": "integer"},
                "hauteur": {"type": "integer"},
                "moteur": {"type": "string", "description": "workbench | cycles"},
                "echantillons": {"type": "integer", "description": "samples Cycles (defaut 48)"},
                "texture": {"type": "string", "description": "PNG de texture (auto-detectee sinon)"},
            },
            "required": ["stl_path"],
        },
        "function": lambda args: tool_blender_render(
            args["stl_path"], args.get("png_path", ""),
            int(args.get("largeur", 600)), int(args.get("hauteur", 450)),
            str(args.get("moteur", "workbench")), int(args.get("echantillons", 48)),
            str(args.get("texture", "")),
        ),
    },
    "pipeline_3d": {
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
        "function": lambda args: __import__("backend.tools.t_pipeline_3d", fromlist=["pipeline_3d"]).pipeline_3d(
            args["request_text"], args.get("save_dir", "")
        ),
    },
}



def ollama_tool_specs(names: list[str]) -> list[dict]:
    specs = []
    for name in names:
        tool = TOOL_DEFINITIONS[name]
        specs.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": tool["description"],
                    "parameters": tool["parameters"],
                },
            }
        )
    return specs



def _resolve_workspace_paths(args: dict) -> dict:
    """Remplace {WORKSPACE_IMAGES} par le chemin reel du dossier images."""
    import os
    from pathlib import Path
    workspace_root = os.environ.get("LOCALAI_ROOT")
    if not workspace_root:
        import os as _os
        if os.name == "nt":
            workspace_root = r"E:\ia"
        else:
            if os.geteuid() == 0:
                workspace_root = "/opt/localai"
            else:
                workspace_root = str(Path.home() / ".localai")
    images_dir = str(Path(workspace_root) / "workspace" / "images")
    resolved = {}
    for k, v in args.items():
        if isinstance(v, str) and "{WORKSPACE_IMAGES}" in v:
            resolved[k] = v.replace("{WORKSPACE_IMAGES}", str(Path(workspace_root) / "workspace" / "images"))
        else:
            resolved[k] = v
    return resolved


def execute_tool(name: str, arguments: dict, permissions: PermissionManager) -> str:
    tool = TOOL_DEFINITIONS.get(name)
    if tool is None:
        return f"Erreur: outil inconnu '{name}'"
    try:
        permissions.check(tool["level"])
    except PermissionDenied as exc:
        return f"PERMISSION REFUSEE: {exc}"
    # Resout les placeholders de chemins workspace
    arguments = _resolve_workspace_paths(arguments)
    try:
        resultat = str(tool["function"](arguments))[:6000]
    except Exception as exc:
        return f"Erreur d'execution de {name}: {exc}"
    # Verification systematique : tout code python livre par write_file est
    # verifie (syntaxe + execution si possible) avant d'etre renvoye a l'agent.
    if name == "write_file" and str(arguments.get("path", "")).endswith(".py"):
        code = str(arguments.get("content", ""))
        _exec = "import bpy" not in code
        verdict = tool_check_code(code, "python", executer=_exec)
        if "ECHEC SYNTAXE" in verdict:
            resultat += (
                "\n\n[CONTROLE AUTOMATIQUE] ECHEC: le fichier Python ecrit ne compile pas!\n"
                + verdict[:600]
            )
        elif not _exec:
            resultat += (
                "\n\n[CONTROLE AUTOMATIQUE] Syntaxe OK (script Blender bpy : "
                "execution via l'outil blender_run, pas en sandbox)."
            )
        elif "exit 1" in verdict:
            resultat += (
                "\n\n[CONTROLE AUTOMATIQUE] Attention: le fichier s'execute avec des erreurs.\n"
                + verdict[:400]
            )
        else:
            resultat += "\n\n[CONTROLE AUTOMATIQUE] Syntaxe OK."
    return resultat
