# Architecture technique — LocalAI

> Source de vérité condensée. Détails d'exploitation : `docs/SECURITE.md`,
> `docs/SKILLS.md`, `docs/DEPLOIEMENT.md`.

## 1. Vue d'ensemble

- **Serveur Rust** `localai-server` (axum) sur `:8320` — API REST + SSE,
  frontend servi en no-store, ~8 Mo RAM, ~0,46 ms p50, ~3 680 req/s.
- **Pont Python** `pybridge.rs` : le venv `E:\ia\.venv` est appelé en
  sous-processus par appel (`python -c`, cwd racine, timeout 1800 s,
  `kill_on_drop`). Aucun listener Python permanent.
- **Ollama** `:11434` — `OLLAMA_MODELS`, `OLLAMA_FLASH_ATTENTION=1`,
  `OLLAMA_KV_CACHE_TYPE=q8_0`. Champion : `localai-prog-latest`
  (Qwen3-8B LoRA R16, Q8, num_ctx 8192, ~30 t/s).
- **Frontend** React 19 + Vite (thème Matrix : pluie canvas, anneau 3D de
  dossiers, HUD live) → `dist/` servi par le Rust.

## 2. Crates Rust

| Crate | Rôle |
|---|---|
| `localai-core` | config (LOCALAI_ROOT), SQLite WAL (db.rs : conversations, messages, tool_traces, memories, feedback/auto_evals/ameliorations), client Ollama |
| `localai-server` | **le serveur** : routes (chat, outil, feedback, conversations, models, documents+RAG, memory, settings, sd, studio, style_collab, extras, system, benchmark, finetune, voice, tts, comms, security, health), tools.rs (boucle 14 rounds, précheck sandbox), pybridge.rs, telegram.rs |
| `localai-pipeline` | CLI cycles : status / bench / promote |
| `localai-watch` | watchdog SYSTEM : santé 8320/11434, SMART, GPU > 85 °C, alertes |

## 3. Boucle chat (`routes/chat.rs`)

1. Persistance conversation + historique (40 derniers).
2. **Routage** : agent explicite → détection d'intention par mots entiers
   (image 2D vs 3D vs bureau vs système vs pont Python `route_agent`).
3. Images jointes → `input/` (base64 ou data URL, détection magic bytes) ;
   intention d'édition → agent image (`edit_image`).
4. Prompt système combiné : agent + RAG (nomic + cosinus) + mémoires (30) +
   **règles d'auto-amélioration (5)** + dossier sandbox + images.
5. Garde-fou image round 0 : force `find_reference_image` (hors style) puis
   **injecte systématiquement** `reference` + `reference_desc` dans
   `generate_image` (fidélité Kontext garantie).
6. Boucle outils : exécution via pont, détection de refus, traces
   (`tool_traces` status ok/denied/compact), troncature 4 000 car, compactage
   au-delà de 80 % de num_ctx.
7. Fin : persistance, événement `done`, **auto-évaluation non bloquante**
   (juge qwen3:8b → `auto_evals` → alimente le coach).

## 4. Pipeline images (`sdgen.py`)

- **Sélection modèle** : flux Q4/Q5 prioritaire (VRAM), kontext jamais par
  défaut (exige une référence), turbo ≤ 12 steps.
- **Sans référence** : FLUX schnell 4 steps, base 16:9 1344×768.
- **Avec référence (sujet réel)** : FLUX Kontext Q6_K en **2 passes**
  (1 : « the same subject as in the reference… » ; 2 : « keep the subject
  exactly identical… ») — euler, cfg 1.0, guidance 3.5, base 1280×720.
- **4K** : Real-ESRGAN x4plus → 3840×2160 (repli Lanczos).
- **QC vision** : note /10 + FIDELE/PAS FIDELE par variante ; régénération
  (graine aléatoire) si PAS FIDELE ; « MEILLEURE IMAGE » en tête.
- **Préchauffage cache** : lecture séquentielle des modèles (~175 Mo/s) avant
  le lancement de sd-cli — les mmap à froid sur SSD QLC chutent à ~20 Mo/s.
- **Verrou GPU global** (msvcrt/fcntl) : une génération à la fois.
- **`_liberer_vram()`** : décharge les modèles Ollama avant SD/vision.

## 5. API jobs (tâches longues)

- `routes/outil.rs` : **jobs asynchrones** — `POST /api/outil/execute`
  `{"tool","args"}` → `{"job"}` ; `GET /api/outil/job/:id` (+ `?attendre=1`
  long-poll 880 s, refais un GET après).
- Le pont Python (`pybridge`) exécute les outils dans des sous-processus avec
  timeout cohérent (1800 s pour les chaînes longues).

## 6. Base de données (SQLite WAL)

`conversations`, `messages`, `memories`, `documents`, `document_chunks`,
`learned_knowledge`, `prompts`, `tool_traces`, `telegram_sessions`,
`app_settings`, `feedback`, `auto_evals`, `ameliorations`.

## 7. Auto-amélioration

- **Feedback utilisateur** : `POST /api/feedback` (rating 1-5).
- **Auto-évaluation** : après chaque réponse à outils, un juge léger note /10
  (non bloquant).
- **Coach hebdomadaire** (`coach.py`, tâche planifiée dimanche 05:00 ou
  `POST /api/selfimprove/coach`) : analyse signaux faibles 7 jours → 1-5
  règles (qwen3:14b, dédoublonnage) → `ameliorations` → **injectées dans le
  prompt système** (LIMIT 5).
- **Cycles fine-tune** : corpus (SFT+RLVR, anti-fuite MinHash) → LoRA
  (unsloth) → merge GGUF → bench holdout figé (100 prompts, double juge,
  Wilson 95 %) → promotion conditionnelle (`promote.py`, 10 conditions).

## 8. Performance — principes

- Tout ce qui est chaud en Rust (HTTP, boucle, gardes), lourd en Python
  (sous-processus), inférence en Ollama.
- Timeouts cohérents de bout en bout : client 900 s ≥ long-poll serveur
  880 s ≥ pont 1800 s (jobs) — aucun maillon plus court que la chaîne.
- Préchauffage cache avant tout chargement SD ; réutilisation des modèles
  Ollama déjà chargés (traduction sur le champion si présent).
- Verrou GPU : évite les OOM concurrents ; file d'attente implicite.

## 9. Premiere installation (onboarding)

- **Route `GET /api/onboarding`** (Rust) : renvoie la langue choisie, l'etat
  d'installation et, via le pont Python (`backend.core.hardware`), le
  materiel detecte et le plan de modeles adapte.
- **Paliers** : `T1` (<= 8 Go VRAM / CPU), `T2` (10-20 Go), `T3` (>= 22 Go).
  Chaque palier definit un modele par role (agent_brain, coder, vision,
  teacher, student, embed) ; le premier modele installe gagne.
- **Assistant frontend** (`OnboardingWizard.tsx`) : langue -> detection
  materielle -> modeles recommandes (installation SSE via `/api/models/install`)
  -> fin. L'etat est persiste (`language`, `onboarded`).
- **Langue des reponses** : le prompt systeme recoit une directive
  (« Reponds toujours en francais », « Always answer in English », etc.)
  construite depuis le reglage `language` — sans toucher aux prompts des agents.
