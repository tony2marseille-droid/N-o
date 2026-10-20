# Outils & agents — Néo

## 1. L'API jobs (tâches longues)

Les générations longues (image 4K, 3D, recherche approfondie) passent par le
mode job : la main est rendue immédiatement, un long-poll attend la fin.

```
Étape 1 — Lancer
POST http://127.0.0.1:8320/api/outil/execute
{"tool":"generate_image","args":{"prompt":"...","reference":"..."}}
→ {"job":"j<hex>","etat":"lance"}

Étape 2 — Récupérer (un seul appel, il attend tout seul)
GET http://127.0.0.1:8320/api/outil/job/<id>?attendre=1
→ {"fini":false}  → refais le même GET
→ {"fini":true,"resultat":"..."}  → transmets tel quel
```

**Pourquoi ce design** : une image 4K fidèle dure 2-10 min (recherche de
référence web + validation vision + Kontext 2 passes + upscale 4K + QC). Le
long-poll (plafond 880 s) attend sans bloquer le client.

## 2. Les outils exposés

| Famille | Outils |
|---|---|
| Image | `generate_image` (référence, variantes, QC), `edit_image`, `analyze_image`, `find_reference_image`, `image_search`, `fetch_image`, `convertir_image` |
| Documents | `document_create` (docx/pdf/xlsx), `deep_research`, `web_search`, `web_fetch`, `telecharger_fichier` |
| 3D | `render_scad`, `slice_model`, `generate_mesh_from_image`, `mesh_info/repair/boolean/fuse`, `render_stl_views`, `validate_mesh/design`, `render_preview`, `blender_run/render` |
| Fichiers/dev | `list_dir`, `read_file`, `write_file`, `run_command`, `check_code`, `rechercher_fichiers` |
| Système | `meteo`, `system_stats`, `volume`, `open_app`, `capture_ecran` |
| Utilitaires | `calculator`, `get_datetime`, `memoire_ajouter`, `notes`, `presse_papiers` |

Chaque outil a un niveau de permission (`SAFE`/`USER`/`POWER`/`ADMIN`) vérifié
avant exécution, et la sandbox confine les écritures au workspace.

## 3. Agents (7)

| Agent | Outils clés |
|---|---|
| `general` | sans outils (conversation) |
| `dev` | fichiers/terminal + deep_research |
| `docs` | RAG natif (vos documents) |
| `image` | generate_image, edit_image, find_reference_image (fidélité Kontext) |
| `bureau` | document_create + recherche web |
| `systeme` | météo, stats, volume, apps, écran |
| `cad3d` | pipeline 3D complet (OpenSCAD/TripoSR/Blender/slice) |
