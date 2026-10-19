# Déploiement — LocalAI sur Windows

> Cible : Windows 10/11, GPU NVIDIA ≥ 8 Go, 32 Go RAM conseillés, SSD.

## 1. Prérequis

| Composant | Version | Usage |
|---|---|---|
| [Ollama](https://ollama.com) | ≥ 0.4 | inférence LLM |
| Rust (rustup) | ≥ 1.89 | build serveur |
| Python | 3.11+ | kernels/outils |
| Node.js | ≥ 20 | build frontend |
| Git | — | sources |

Variables d'environnement (HKCU) :
```
LOCALAI_ROOT=E:\ia
OLLAMA_MODELS=E:\LocalAI-Modeles
OLLAMA_FLASH_ATTENTION=1
OLLAMA_KV_CACHE_TYPE=q8_0
OPENJARVIS_SSRF_ALLOW=127.0.0.1,192.168.50.124,localhost
```

## 2. Modèles (Ollama)

```
ollama pull qwen3:8b            # dispatch rapide
ollama pull qwen2.5vl:7b        # vision
ollama pull nomic-embed-text    # embeddings RAG
# fine-tune maison (optionnel) : importer le GGUF via un Modelfile
```

Modèles SD (stable-diffusion.cpp, dossier `E:\ia\tools\sd\models`) :
`flux1-schnell-Q4_K_S.gguf` (défaut), `t5xxl-q8_0.gguf`, `clip_l.safetensors`,
`ae.safetensors`, `flux1-kontext-dev-Q6_K.gguf` (fidélité sujet).

Outils externes : Real-ESRGAN (ncnn-vulkan), OpenSCAD, PrusaSlicer, Blender,
TripoSR — paths dans `backend/tools/common.py` (surcharges `LOCALAI_*`).

## 3. Build

```bat
cd frontend && npm install && npm run build
cd ..\localai-rs && cargo build --release
target\release\localai-server.exe   → http://localhost:8320
```

## 4. Tâches planifiées (production)

| Tâche | Déclencheur | Action |
|---|---|---|
| `LocalAI_server_boot` | au boot | serveur Rust :8320 |
| `LocalAI_Ollama_boot` | au boot | `ollama serve` (attend 60 s, vérifie l'API) |
| `LocalAI_server_watch` | 5 min | relance 8320 si down |
| `LocalAI_watch_rs` | service SYSTEM | SMART, GPU > 85 °C, alertes |
| `LocalAI_coach` | dimanche 05:00 | cycle d'auto-amélioration |

⚠️ Désactiver l'auto-update d'Ollama OU prévoir le repair (un update raté
peut laisser une install amputée — symptom : 500 « llama-server binary not
found » sur tout chat ; solution : recopier `lib\ollama\*` depuis le zip
officiel).

## 5. Client vocal LAN (« Hey Jarvis »)

`voice/jarvis_local.py` : wake-word openWakeWord + VAD + STT serveur +
`/api/chat` + TTS SAPI. Modes wake/push/console.

```
pip install openwakeword sounddevice soundfile httpx numpy
python jarvis_local.py --url http://IP_SERVEUR:8320 --mode wake
```

Le micro et les haut-parleurs restent côté client ; STT/TTS côté serveur.

## 6. Pont OpenJarvis (optionnel)

1. Cloner https://github.com/open-jarvis/OpenJarvis puis `uv sync`.
2. Python 3.11 dédié hors AppData (protection « untrusted mount point ») :
   `setx UV_PYTHON_INSTALL_DIR E:\LocalAI-cache\uv-python`.
3. Config : `~/.openjarvis/config.toml` (voir docs/SKILLS.md §4).
4. Copier le skill : `docs/skills/localai/SKILL.md` →
   `~/.openjarvis/skills/localai/SKILL.md`.
5. Extension native : `uv run maturin develop --release --manifest-path
   rust/crates/openjarvis-python/Cargo.toml`.
6. Test : `jarvis ask "Quel temps fait-il a Paris ? Utilise le skill localai."`

## 7. Vérification post-install

```bash
curl http://localhost:8320/api/health          # {"status":"ok"}
curl http://localhost:8320/api/agents          # 7 agents
curl http://localhost:11434/api/version        # ollama
# génération test :
curl -X POST http://localhost:8320/api/outil/execute \
  -H "Content-Type: application/json" \
  -d '{"tool":"generate_image","args":{"prompt":"un cube rouge","resolution":"hd"}}'
# puis GET /api/outil/job/<id> jusqu'à fini:true
```
