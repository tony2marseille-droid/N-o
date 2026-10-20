# Néo — Assistant IA 100 % local

> Chat, agents à outils, génération d'images 4K photoréalistes fidèles au sujet,
> 3D/STL imprimable, documents bureautiques, RAG, voix française (mot d'éveil),
> auto-amélioration continue — **aucun cloud, aucune clé API, tout sur votre PC.**

LocalAI est un assistant IA personnel auto-hébergé qui tourne entièrement en
local : un **serveur Rust** ultra-léger (8 Mo de RAM, 3 680 req/s), un backend
Python spécialisé (kernels SD/TTS/STT/3D), **Ollama** pour l'inférence et un
fine-tune maison. L'ensemble est orchestré comme un majordome numérique :
agents spécialisés, pont vocal, auto-amélioration par cycles d'entraînement.

---

## ⚡ Fonctionnalités

| Domaine | Ce que ça fait |
|---|---|
| **Chat** | SSE streaming, 7 agents (général, dev, docs/RAG, image, bureau, système, 3D), 41 outils, boucle tool-calling 14 rounds, compactage de contexte |
| **Images 4K** | FLUX.1-schnell (rapide) / **FLUX Kontext en 2 passes** : recherche d'une photo de référence sur le web → validation par le modèle vision → génération fidèle au sujet réel → upscale Real-ESRGAN 3840×2160 → **contrôle qualité vision automatique** (note /10 + fidélité, régénération si échec) |
| **Édition d'image** | img2img Kontext (« change le ciel », « recolore », « supprime… ») |
| **Documents** | Markdown → Word / PDF / Excel (python-docx, reportlab, openpyxl) + recherche approfondie web multi-étapes |
| **3D / Impression** | OpenSCAD paramétrique, TripoSR (photo → mesh), réparations/booléens/fusions, rendus Blender, tranchage G-code, contrôle qualité sur 4 vues |
| **Système** | météo (Open-Meteo), stats PC/GPU, volume, ouverture d'apps, analyse d'écran par vision |
| **Voix** | mot d'éveil vocal (openWakeWord), STT Whisper, TTS SAPI français, client LAN portable |
| **Auto-amélioration** | feedback utilisateur (1-5) + auto-évaluation (juge interne) + coach hebdomadaire qui transforme les faiblesses en règles injectées dans le prompt |
| **Fine-tuning maison** | corpus → LoRA (unsloth) → merge → GGUF → bench holdout figé (100 prompts, double juge, Wilson 95 %) → promotion conditionnelle |
| **Installation guidée** | Assistant de première installation : choix de la langue (FR/EN/ES/DE/IT), détection du matériel, plan de modèles adapté (petits en T1, grands en T3), installation en un clic |

## 🏗️ Architecture (résumé)

```
┌────────────────────────────────────────────────────────────┐
│  Navigateur (UI Matrix) / Client vocal LAN / Telegram      │
└──────────────────────────┬─────────────────────────────────┘
                           │ :8320 (SSE + REST)
┌──────────────────────────▼─────────────────────────────────┐
│  SERVEUR RUST (axum) — 8 Mo RAM, 0,46 ms p50, 3680 req/s   │
│  routage agents · boucle outils · gardes-fous · jobs       │
│  auto-évaluation · feedback/coach · télémétrie             │
└───────────┬───────────────────────────────┬────────────────┘
            │ pont sous-processus            │ HTTP
┌───────────▼───────────────┐   ┌────────────▼───────────────┐
│  PYTHON (41 outils)       │   │  OLLAMA :11434             │
│  sdgen · t_image · t_cad  │   │  localai-prog-latest       │
│  t_mesh · t_blender       │   │  (Qwen3-8B LoRA R16 Q8)    │
│  t_docs · t_systeme         │   │  qwen2.5vl (vision)        │
│  t_extra · t_web            │   │  qwen3:8b (dispatch)       │
└───────────────────────────┘   └────────────────────────────┘
            │
   stable-diffusion.cpp · Real-ESRGAN · TripoSR · Blender · Whisper
```

**Verrou GPU global** (une génération à la fois), **sandbox double couche**
(Rust + Python), **jobs asynchrones** pour les tâches longues, **préchauffage
cache** des modèles SD (65 s au lieu de 10-30 min sur SSD QLC).

## 📊 Chiffres

| Mesure | Python (avant) | **Rust (production)** |
|---|---|---|
| RAM serveur | 34,8 Mo | **8,1 Mo** |
| Latence p50 | 0,90 ms | **0,46 ms** |
| Débit (8 co) | 1 458 req/s | **3 680 req/s** |
| Empreinte | venv 138 paquets | **exe 7 Mo** |

👉 Détails complets : [`docs/COMPARATIFS.md`](docs/COMPARATIFS.md) ·
Architecture : [`docs/TECHNIQUE.md`](docs/TECHNIQUE.md) ·
Outils : [`docs/SKILLS.md`](docs/SKILLS.md) ·
Sécurité : [`docs/SECURITE.md`](docs/SECURITE.md) ·
Installation : [`docs/DEPLOIEMENT.md`](docs/DEPLOIEMENT.md)

## 🚀 Démarrage rapide

1. **Prérequis** : Windows 10/11, RTX 8 Go+, 32 Go RAM conseillés, Ollama, Rust (build), Python 3.11+.
2. Suivre [`docs/DEPLOIEMENT.md`](docs/DEPLOIEMENT.md) (serveur, venv, modèles, tâches planifiées).
3. `cargo build --release` puis lancer `localai-server.exe` → **http://localhost:8320**.

## 📁 Structure du dépôt

```
code/
├── localai-rs/     # Serveur Rust (4 crates : core, server, pipeline, watch)
├── backend/        # Kernels Python (41 outils, SD, RAG, TTS/STT)
├── frontend/       # UI React/Vite (thème Matrix, accueil animé)
└── scripts/        # Exploitation (rebuild, boot, surveillance)
docs/               # Documentation technique complète
```

## 🇫🇷 Langue

Le projet est pensé **francophone d'abord** : interface, prompts d'agents,
TTS/STT, documents générés — tout est en français par défaut (traduction
automatique FR→EN pour les prompts de génération d'images).

## ⚠️ Note

Ce dépôt documente et partage une architecture **personnelle fonctionnelle**.
Les modèles (GGUF, fine-tunes) ne sont pas inclus (téléchargeables via Ollama
et Hugging Face). Les chemins font référence à `E:\ia` — adaptez à votre
machine via les variables d'environnement `LOCALAI_*`.

---

*Développé avec passion, en local, sans cloud.*
---

*Â© 2026 â€” NÃ©o. Code original publiÃ© sans licence : tous droits rÃ©servÃ©s Ã 
l'auteur. Voir `docs/DEPENDANCES.md` pour les licences des dÃ©pendances et
des modÃ¨les.*
