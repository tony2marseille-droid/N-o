# Publier ce dépôt sur GitHub — pas à pas

## 1. Ce que contient ce dossier

```
I:\ia\export_github\
├── README.md            ← résumé complet du projet (page d'accueil GitHub)
├── LICENSE              ← MIT + remerciements
├── .gitignore           ← exclut modèles, données, caches
├── docs\
│   ├── COMPARATIFS.md   ← AVANT/APRÈS Rust + AVANT/APRÈS fusion (tableaux)
│   ├── TECHNIQUE.md     ← architecture détaillée
│   ├── SKILLS.md        ← pont OpenJarvis + les 41 outils + agents
│   ├── SECURITE.md      ← sandbox, verrou GPU, SSRF, Defender
│   ├── DEPLOIEMENT.md   ← installation pas à pas
│   └── skills\localai\SKILL.md   ← le skill-pont (copie déployée)
├── code\
│   ├── localai-rs\      ← sources Rust (4 crates + manifests)
│   ├── backend\         ← Python (41 outils, SD, RAG, TTS/STT)
│   ├── frontend\        ← React (thème Matrix)
│   └── scripts\         ← exploitation (boot, watch, rebuild)
└── assets\              ← (vide) pour tes captures d'écran
```

## 2. Rafraîchir les sources depuis la production

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File export_github.ps1
```

## 3. Créer le repo GitHub

1. GitHub → New repository → nom : `localai` (ou autre) → **Private** (ou Public) → ne pas initialiser avec README.
2. Sur le serveur :

```bat
cd /d I:\ia\export_github
git init -b main
git add .
git commit -m "LocalAI : assistant IA local complet (Rust + Python + fusion OpenJarvis)"
git remote add origin https://github.com/<TON_COMPTE>/<REPO>.git
git push -u origin main
```

## 4. Conseils

- **Ajoute des captures** dans `assets\` (accueil Matrix, chat, image 4K, STL) — GitHub affiche `assets/*.png` si tu les référence dans le README : `![Accueil](assets/accueil.png)`.
- **Private conseillé** tant que les documents citent ton IP LAN (192.168.50.124) — ou remplace-la par `<LAN_IP>` avant publication publique.
- Les modèles GGUF ne sont pas inclus (voir .gitignore) — le README indique comment les obtenir.
- Pour les mises à jour : relance `export_github.ps1` puis `git add . && git commit -m "maj" && git push`.

## 5. Identité git (si première utilisation)

```bat
git config --global user.name "Ton Nom"
git config --global user.email "ton@email"
```
