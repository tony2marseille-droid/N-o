# Comparatifs Néo — avant/après Rust

> Mesures réelles issues de l'exploitation (benchmarks holdout, smoke tests,
> mesures live nvidia-smi/Ollama). Dernière mise à jour : 2026-10-20.

---

## 1. AVANT / APRÈS — Migration du serveur Python → Rust

### Contexte
L'assistant tournait d'abord sur un backend **FastAPI/Python** (uvicorn) qui
portait à la fois l'API HTTP et la logique. La migration a remplacé la couche
HTTP par un **serveur Rust (axum)** qui pilote les kernels Python comme
sous-processus (pont `pybridge`), tout en conservant un contrat d'API
identique (25/26 endpoints au test contractuel, écart : un champ nullable).

### Tableau comparatif mesuré

| Mesure | Python/FastAPI (avant) | **Rust/axum (après)** | Gain |
|---|---|---|---|
| RAM du serveur | 34,8 Mo | **8,1 Mo** | **×4,3 plus léger** |
| Latence p50 | 0,90 ms | **0,46 ms** | **−49 %** |
| Débit (8 connexions) | 1 458 req/s | **3 680 req/s** | **×2,5** |
| Empreinte logicielle | venv 138 paquets | **binaire 7 Mo** | déploiement atomique |
| Cold load GGUF 10 Go | — | 7-10 s à froid / 0,1 s chaud | (SSD + Defender exclu) |
| Fiabilité processus | fuites/holders intermittents | kill_on_drop, timeouts stricts | stabilité |
| Tests | pytest only | + cargo test, smoke 25/25, sandbox 17/17 | CI locale |
| Télémétrie | logs | traces SQLite (tool_traces, compactage, refus) | observabilité |

### Ce que le Rust a permis (au-delà des chiffres)

| Capacité | Avant | Après |
|---|---|---|
| Boucle tool-calling | Python (GIL, lente) | **14 rounds natifs**, SSE `tool_exec` temps réel |
| Garde-fous sandbox | Python seul | **double contrôle Rust + Python** (précheck avant pont) |
| Telegram | — | **poller natif Rust** (long-poll 50 s, sessions en base) |
| Watchdog | scripts | **localai-watch (SYSTEM)** : santé 8320/11434, SMART, GPU>85 °C, alertes Telegram |
| Compactage contexte | — | au-delà de 80 % de num_ctx, sorties d'outils résumées (traçées) |
| PGO | — | re-profile 4 235 requêtes → **+59 % de débit** |
| Frontend | servi par Python | servi par le binaire (dist/ no-store) |

### Ce qui reste Python (et pourquoi)

Les kernels spécialisés restent en Python — le Rust les appelle en
sous-processus avec timeout strict (1800 s) et kill_on_drop :

| Kernel | Bibliothèque | Rôle |
|---|---|---|
| sdgen | stable-diffusion.cpp (CLI) | FLUX schnell/Kontext, Real-ESRGAN, verrou GPU, préchauffage cache |
| t_image | httpx + PIL | recherche de référence, vision, QC fidélité, variantes |
| t_mesh/t_cad/t_blender | trimesh, OpenSCAD, Blender | 3D : mesh, booléens, rendus, G-code |
| stt/tts | faster-whisper, pyttsx3 | voix française |
| rag/vector_store | numpy + SQLite | embeddings nomic, BM25, RRF |

> **Verdict : la migration Rust a divisé la latence par 2, le débit a été
> multiplié par 2,5, la RAM par 4 — et a apporté les gardes-fous, la
> télémétrie et le watchdog. Le Python reste là où il est irremplaçable
> (écosystème ML).**

---

