# Dépendances tierces & licences — Néo

> Ce document recense ce que le projet **utilise**. Le code de Néo lui-même
> est 100 % original et publié **sans licence** (tous droits réservés à
> l'auteur). Les éléments ci-dessous conservent leurs licences respectives —
> les utiliser n'oblige à rien tant que le projet reste privé.

## 1. Notre code : 100 % original

Vérifié par balayage automatique (2026-10-20) :
- **aucun** en-tête copyright tiers dans `backend/`, `frontend/`, `localai-rs/` ;
- **aucun** marqueur « proprietary / confidential / all rights reserved » ;
- aucun code vendored/copié d'un autre projet.

## 2. Bibliothèques Python (179 paquets, venv)

| Licence | Part | Exemples |
|---|---|---|
| MIT / BSD / Apache-2.0 / PSF | ~95 % | torch, transformers, diffusers, trimesh, rembg, faster-whisper, httpx, gradio, unsloth, reportlab, openpyxl, python-docx, pypdf |
| MPL-2.0 (copyleft par fichier) | 3 | pyttsx3, orjson, tqdm, certifi |
| LGPL (copyleft faible, bibliothèque isolée) | 2 | pycountry (LGPL-2.1), unsloth_zoo (LGPL-3) |
| GPL-3 (copyleft fort, bibliothèque) | 1 | pymeshlab (traitement de mesh 3D) |
| Runtime Microsoft | 1 | msvc_runtime (DLL VC++ redistribuables Microsoft — gratuit, standard Windows, pas du code du projet) |

**Ce que ça implique** : en usage privé, aucune contrainte. Si le projet est
un jour **distribué compilé**, il faudrait écarter les copyleft — pymeshlab
est remplaçable (trimesh + fast-simplification, déjà présents, font le même
travail pour nos usages) ; les LGPL se satisfont d'un avertissement.

## 3. Crates Rust (201)

Tout l'écosystème standard **MIT / Apache-2.0** (axum, tokio, serde, reqwest,
hyper, rusqlite, chrono, clap…). Aucun copyleft. Aucune obligation sur notre
code, même en distribution.

## 4. Outils externes (processus séparés)

Appelés comme des **exécutables indépendants** — aucune contamination de
licence, même en distribution binaire :

| Outil | Licence | Usage |
|---|---|---|
| Ollama | MIT | serveur LLM |
| stable-diffusion.cpp | MIT | génération d'images |
| llama.cpp | MIT | conversion GGUF |
| Real-ESRGAN (ncnn) | BSD-3 | upscale 4K |
| TripoSR | MIT | image → mesh 3D |
| faster-whisper | MIT | transcription |
| openWakeWord | Apache-2.0 | mot d'éveil |
| ffmpeg | GPL/LGPL | montage vidéo (processus séparé) |
| Blender | GPL | rendus 3D (processus séparé) |
| OpenSCAD | GPL-3 | CAO paramétrique (processus séparé) |
| PrusaSlicer | AGPL-3 | tranchage G-code (processus séparé) |

## 5. Modèles (poids) — les vraies conditions à connaître

| Modèle | Licence | Usage personnel | Usage commercial |
|---|---|---|---|
| Qwen3-8B (base du fine-tune), qwen3:8b/14b | Apache-2.0 | ✓ | ✓ |
| Qwen2.5-VL (vision) | Apache-2.0 | ✓ | ✓ |
| FLUX.1-schnell (images rapides) | Apache-2.0 | ✓ | ✓ |
| DeepSeek-R1 | MIT | ✓ | ✓ |
| nomic-embed-text (RAG) | Apache-2.0 | ✓ | ✓ |
| Whisper small (STT) | MIT | ✓ | ✓ |
| **FLUX.1 Kontext dev (fidélité photo)** | **non-commerciale (BFL)** | ✓ | ✗ licence requise |
| **Hunyuan3D-2.1 / hy3dgen** | Tencent Hunyuan Community | ✓ | conditions à vérifier |
| gemma3 | Gemma Terms of Use | ✓ | conditions à vérifier |
| llama3.1 | Llama Community License | ✓ | conditions à vérifier |

**En pratique** : en usage 100 % privé et local (le cas aujourd'hui), tout
est conforme. Si le projet devient un **produit commercial**, deux modèles
exigeront une licence payante ou un remplacement : FLUX.1 Kontext dev (le
cœur de la fidélité photo — remplaçable par schnell ou un modèle Apache) et
Hunyuan3D-2.1 (remplaçable par TripoSR, MIT).
