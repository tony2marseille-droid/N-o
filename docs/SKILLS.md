# Skills & pont OpenJarvis — LocalAI

## 1. Le skill `localai` (standard agentskills.io)

Un skill = un dossier `SKILL.md` avec frontmatter YAML + instructions.
Installez-le dans `~/.openjarvis/skills/localai/SKILL.md`.

```markdown
---
name: "localai"
description: "Cerveau LocalAI local (port 8320) : images 4K photoréalistes
  fidèles au sujet, documents Word/PDF/Excel, 3D/STL, météo, stats PC, volume,
  apps, écran, recherche approfondie. Un seul appel exécute tout le workflow."
version: "2.1.0"
author: "localai"
tags: [localai, image, documents, 3d, systeme]
user_invocable: true
---
```

### Le flux en 2 appels (jobs + long-poll)

```
Étape 1 — Lancer
POST http://127.0.0.1:8320/api/outil/execute
{"tool":"localai_chat","args":{"message":"<demande complète de l'utilisateur>"}}
→ {"job":"j<hex>","etat":"lance"}

Étape 2 — Récupérer (un seul appel, il attend tout seul)
GET http://127.0.0.1:8320/api/outil/job/<id>?attendre=1
→ {"fini":false}  → refais le même GET
→ {"fini":true,"resultat":"..."}  → transmets tel quel
```

**Pourquoi ce design** : une image 4K fidèle dure 2-10 min (recherche de
référence web + validation vision + Kontext 2 passes + upscale 4K + QC). Le
mode job rend la main immédiatement et le long-poll attend sans consommer de
tours ReAct.

## 2. Les 41 outils LocalAI exposés

| Famille | Outils |
|---|---|
| Image | `generate_image` (référence, variantes, QC), `edit_image`, `analyze_image`, `find_reference_image`, `image_search`, `fetch_image`, `convertir_image` |
| Documents | `document_create` (docx/pdf/xlsx), `deep_research`, `web_search`, `web_fetch`, `telecharger_fichier` |
| 3D | `render_scad`, `slice_model`, `generate_mesh_from_image`, `mesh_info/repair/boolean/fuse`, `render_stl_views`, `validate_mesh/design`, `render_preview`, `blender_run/render` |
| Fichiers/dev | `list_dir`, `read_file`, `write_file`, `run_command`, `check_code`, `rechercher_fichiers` |
| Système | `meteo`, `system_stats`, `volume`, `open_app`, `capture_ecran` |
| Utilitaires | `calculator`, `get_datetime`, `memoire_ajouter`, `notes`, `presse_papiers` |
| Pont | `localai_chat` (méta-outil : exécute toute demande via la boucle agent) |

Chaque outil a un niveau de permission (`SAFE`/`USER`/`POWER`/`ADMIN`) vérifié
avant exécution, et la sandbox confine les écritures au workspace.

## 3. Agents LocalAI (7)

| Agent | Outils clés |
|---|---|
| `general` | sans outils (conversation) |
| `dev` | fichiers/terminal + deep_research |
| `docs` | RAG natif (vos documents) |
| `image` | generate_image, edit_image, find_reference_image (fidélité Kontext) |
| `bureau` | document_create + recherche web |
| `systeme` | météo, stats, volume, apps, écran |
| `cad3d` | pipeline 3D complet (OpenSCAD/TripoSR/Blender/slice) |

## 4. Config OpenJarvis recommandée

```toml
[engine]
default = "ollama"

[intelligence]
default_model = "qwen3:8b"   # dispatcher rapide ; le cerveau finetuné vit dans LocalAI

[agent]
default_agent = "native_react"
max_turns = 8
tools = "http_request,web_search,web_fetch,calculator,think"

[skills]
enabled = true

[security]
profile = "personal"
```

## 5. Correctifs locaux OpenJarvis (à reporter en amont si pertinent)

1. **SSRF** : `security/ssrf.py::check_ssrf` honore une liste blanche
   d'environnement `OPENJARVIS_SSRF_ALLOW="127.0.0.1,192.168.50.124,localhost"`
   (sinon la protection bloque tout appel au serveur local, y compris loopback).
2. **http_request** : timeout défaut 600 s et `ToolSpec.timeout_seconds = 900`
   (le timeout par appel du client n'est pas fiable ; c'est la spec qui prime
   dans l'exécuteur).
3. **Extension native** : `uv run maturin develop --release --manifest-path
   rust/crates/openjarvis-python/Cargo.toml` → rate-limiter + SSRF natifs.
