# Sécurité — LocalAI

## 1. Sandbox double couche (fichiers & commandes)

1. **Précontrôle Rust** (`tools.rs::precheck`) AVANT le pont :
   - refus de `..` (traversée), chemins absolus hors racines autorisées ;
   - `run_command` : liste blanche d'exécutables (python, node, git,
     openscad, dir, type, findstr), refus des caractères `| & ; > < \``
     et de la substitution `$()` ;
   - racines : écriture = `E:\ia\workspace` ; lecture = workspace +
     `E:\ia\data\documents` (configurables dans `data/security_rules.json`).
2. **Sandbox Python** (`t_files.py`, `common.py`) : résolution anti-junction,
   extensions interdites, exécution sans shell, timeout 60 s, sortie tronquée
   à 20 000 caractères, journal des refus.
3. Détection des refus côté chat (`refusé…`, `ACCES REFUSE`,
   `COMMANDE BLOQUEE`) → `tool_traces.status='denied'` + alerte watchdog
   si ≥ 5 refus/10 min (Telegram).
4. Tests : `tests/test_sandbox.py` (17 cas), `test_safe_code_exec.py` (6).

## 2. Permissions d'outils

Chaque outil déclare un niveau : `SAFE` < `USER` < `POWER` < `ADMIN`.
Le niveau courant vient du réglage `tool_level` (défaut `USER`). Un appel
sous le niveau requis est refusé AVANT exécution (message
`PERMISSION REFUSEE`).

## 3. Verrou GPU global

Fichier lock inter-processus (`msvcrt`/`fcntl`) : **une seule génération SD
à la fois** — évite les OOM (les modèles FLUX chargent ~10 Go). File
d'attente implicite, timeout d'attente 1 h.

## 4. Réseau

- Serveur Rust : écoute locale/LAN (`0.0.0.0:8320`) — pare-feu Windows avec
  règle dédiée ; aucun service n'expose le système de fichiers au-delà des
  racines sandbox.
- `/api/outil/execute` exécute des outils au niveau configuré — même surface
  que `/api/chat` (même zone de confiance LAN). Pour une exposition plus
  large : préférer une clé d'API reverse-proxy.

## 5. Secrets

- Aucune clé cloud requise (météo Open-Meteo sans clé, DuckDuckGo public).
- Les tokens (Telegram/LiveKit) vivent en base (`app_settings`), jamais dans
  le code. Ne jamais versionner `data/localai.db`.
- Elévation : `LocalAccountTokenFilterPolicy=1` est requis pour
  l'administration SSH — à réserver à un LAN de confiance.

## 6. Défender / AV

- Exclusions recommandées (les compilateurs et loaders ML génèrent des faux
  positifs massifs) : `E:\ia`, `E:\LocalAI-Modeles`,
  `E:\localai-rs`, `E:\LocalAI-tmp` + processus `sd-cli.exe`,
  `llama-server.exe`, `ollama.exe`.
- Sans exclusions, les chargements de modèles peuvent chuter à ~20 Mo/s
  (scanning temps réel) — le préchauffage cache n'y remédie pas seul.

## 7. Journalisation

- `data/security_log.jsonl` : accès refusés, outils refusés, événements.
- `tool_traces` (SQLite) : chaque appel d'outil (args, aperçu, statut,
  raison) — base de l'auto-amélioration et de l'audit.
