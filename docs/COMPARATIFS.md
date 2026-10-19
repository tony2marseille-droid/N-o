# Comparatifs LocalAI — avant/après Rust · avant/après fusion OpenJarvis

> Mesures réelles issues de l'exploitation (benchmarks holdout, smoke tests,
> mesures live nvidia-smi/Ollama). Dernière mise à jour : 2026-10-19.

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

## 2. AVANT / APRÈS — Fusion avec OpenJarvis (Stanford)

### Contexte
OpenJarvis (open-jarvis/openjarvis, Stanford Hazy Research) est un framework
d'agents personnels : skills (standard agentskills.io), canaux de
communication, orchestrateurs, boucle d'apprentissage, télémétrie énergétique.
La fusion garde **le meilleur des deux** : OpenJarvis orchestre (dispatch
rapide, skills, canaux), **LocalAI reste le cerveau** (fine-tune maison,
41 outils, pipelines images/3D, auto-amélioration).

### Tableau de capacités

| Capacité | LocalAI seul (avant) | **Après fusion** | Apport |
|---|---|---|---|
| Cerveau LLM | fine-tune maison (30 t/s, Q8) | **inchangé** (son territoire) | qualité conservée |
| Orchestration externe | — | dispatcher **qwen3:8b** (110 t/s, ReAct) | rapidité |
| Compétences (skills) | — | standard **agentskills.io** + catalogues (Hermes ~150, OpenClaw ~13 700) | extensibilité |
| Canaux de communication | UI web + vocal LAN | + **webchat/API OpenAI** ; Telegram/Discord/WhatsApp… (sur tokens) | portée |
| Agents planifiés | tâches Windows | + **scheduler OpenJarvis** (morning digest, deep research, monitoring) | autonomie |
| API publique | REST maison :8320 | + **compatible OpenAI** :8000 (intégrations tierces) | interop |
| MCP | — | client/serveur MCP | écosystème |
| Évaluations | holdout + double juge | + télémétrie énergie/FLOPs/coût (Energy: nvidia hw_counter) | mesure |
| Routage | mots-clés + routeur adaptatif opt-in | + routage par **complexité** (tier: trivial/simple/…) | finesse |
| Mémoire | table memories + RAG | inchangée (son territoire) | — |

### Tableau des scores (pondéré pour un usage personnel francophone, /10)

| Dimension | Poids | LocalAI seul | **Fusionné** | Δ |
|---|---|---|---|---|
| Orchestration (agents/skills/planification) | 15 % | 9,0 | 9,5 | +6 % |
| Canaux distants | 10 % | 2,0 | 8,5* | ×4 |
| Génération images 4K/Kontext | 15 % | 9,5 | 9,5 | = |
| Voix FR | 10 % | 8,0 | 8,0 | = |
| Documents/3D | 8 % | 8,0 | 8,0 | = |
| Auto-amélioration | 10 % | 7,5 | 9,0 | +20 % |
| Efficacité mono-GPU | 12 % | 8,5 | 8,5 | = |
| Français natif | 10 % | 9,5 | 9,5 | = |
| Interop (MCP/A2A/OpenAI) | 5 % | 3,0 | 9,0 | ×3 |
| Légèreté | 5 % | 8,5 | 7,5 | −12 % (processus suppl.) |
| **Composite** | 100 % | **7,7** | **8,8** | **+14 %** |

\* Canaux : webchat/API immédiats ; messageries sur tokens d'utilisateur.

### Architecture de la fusion (le pont)

```
OpenJarvis (E:\OpenJarvis, :8000)
  agent native_react (qwen3:8b, 8 tours)
    └── skill localai (agentskills.io)
          └── POST /api/outil/execute {"tool":"localai_chat", ...}   → job id
          └── GET  /api/outil/job/<id>?attendre=1                     → long-poll 880 s
                └── LocalAI (Rust :8320)
                      cerveau localai-prog-latest
                      41 outils · référence+Kontext+QC · sandbox · verrou GPU
```

| Mécanisme | Détail | Pourquoi |
|---|---|---|
| **Jobs asynchrones** | POST rend un id immédiatement | les générations durent 2-10 min : aucun HTTP synchrone ne survit |
| **Long-poll** | `?attendre=1` bloque jusqu'à 880 s (refais un GET si besoin) | 1 seul appel par job côté agent |
| **Timeouts cohérents** | tool http_request 900 s · pont Python 1800 s · httpx interne 1700 s | aucun maillon plus court que la chaîne |
| **Préchauffage cache** | lecture séquentielle des modèles (≈175 Mo/s) avant sd-cli | mmap froid QLC = 20 Mo/s (10-30 min perdus) |
| **SSRF maîtrisé** | liste blanche `OPENJARVIS_SSRF_ALLOW` (127.0.0.1, LAN) | la protection SSRF d'OpenJarvis bloquait le serveur local |
| **Extension Rust native** | openjarvis_rust (maturin) | rate-limiter + SSRF au niveau natif |

### E2E vérifié (2026-10-19)

| Test | Résultat |
|---|---|
| `jarvis ask` (chat direct) | ✓ réponse française, fine-tune via Ollama |
| Météo via skill → job | ✓ résultat réel (Open-Meteo) |
| Image 4K SR-71 via job | ✓ **note 9/10 FIDÉLITÉ**, QC vision, fichier 4K livré |
| Réponse agent finale | ✓ chemin + note + description en français |
| API OpenAI-compatible | ✓ `/v1/chat/completions` + routage complexité |
| Extension Rust | ✓ build maturin 1 m 28 s, import OK |

### Limites honnêtes

- Pipeline Kontext complet via OpenJarvis : ~15-25 min à froid (ping-pong
  VRAM LLM/vision/SD sur 10 Go) — fonctionne, l'optimisation continue
  (préchauffage + réutilisation de modèles chargés déjà en place).
- Canaux de messagerie : nécessitent des tokens de bot créés par
  l'utilisateur (BotFather / Discord Developer Portal).
- Deux frameworks = deux surfaces de maintenance ; le pont est volontairement
  **mince** (1 skill + 2 endpoints) pour limiter le couplage.
