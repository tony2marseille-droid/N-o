"""Detection materielle et plans de modeles adaptes.

Classe la machine en 3 paliers selon la VRAM du GPU et assigne un modele
par ROLE (cerveau agent, codeur, vision, professeur de distillation...).
Chaque role est une liste de preferences : le premier modele installe gagne.
"""

import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

PROFILE_PATH = Path("data/hardware_profile.json")

MODEL_PLANS: dict[str, dict[str, list[str]]] = {
    "T1": {
        "agent_brain": ["qwen3:8b", "qwen3:4b", "qwen2.5:7b"],
        "coder": ["qwen2.5-coder:7b", "qwen3:4b"],
        "vision": ["qwen2.5vl:7b", "llama3.2-vision:11b", "gemma3:4b"],
        "teacher": ["qwen3:14b", "qwen3:8b"],
        "student": ["gemma3:4b", "qwen3:4b"],
        "embed": ["nomic-embed-text", "all-minilm"],
    },
    "T2": {
        "agent_brain": ["qwen3:14b", "deepseek-r1:8b", "qwen2.5:14b", "mistral-small"],
            "coder": ["localai-prog-latest:latest", "localai-prog-v9.2-q5km:latest", "qwen2.5-coder:14b"],
        "vision": ["qwen2.5vl:7b", "llama3.2-vision:11b"],
        "teacher": ["qwen3:14b", "qwen3:30b"],
        "student": ["qwen3:8b", "qwen3:4b"],
        "embed": ["nomic-embed-text", "mxbai-embed-large"],
    },
    "T3": {
        "agent_brain": ["qwen3:30b", "qwen3:14b", "qwen2.5:32b"],
        "coder": ["qwen2.5-coder:32b", "qwen2.5-coder:14b"],
        "vision": ["qwen2.5vl:14b", "qwen2.5vl:7b"],
        "teacher": ["qwen3:30b", "qwen3:14b"],
        "student": ["qwen3:8b", "gemma3:4b"],
        "embed": ["mxbai-embed-large", "nomic-embed-text"],
    },
}


def detect_gpu() -> list[dict]:
    """Liste les GPU NVIDIA via nvidia-smi (vide si absent)."""
    try:
        out = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,driver_version",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout.strip()
    except Exception:
        return []
    gpus = []
    for line in out.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 2:
            try:
                gpus.append(
                    {
                        "name": parts[0],
                        "vram_gb": round(int(parts[1]) / 1024, 1),
                        "driver": parts[2] if len(parts) > 2 else "",
                    }
                )
            except ValueError:
                continue
    return gpus


def detect_ram_gb() -> float:
    try:
        import psutil

        return round(psutil.virtual_memory().total / (1024**3), 1)
    except Exception:
        pass
    try:
        out = subprocess.run(
            ["wmic", "ComputerSystem", "get", "TotalPhysicalMemory"],
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout
        return round(int(out.split()[1]) / (1024**3), 1)
    except Exception:
        return 0.0


def detect_disk_gb() -> dict:
    """Espace libre/total du disque d'installation."""
    try:
        u = shutil.disk_usage(Path(__file__).resolve().anchor or "C:\\")
        return {"free_gb": round(u.free / 1024**3, 1), "total_gb": round(u.total / 1024**3, 1)}
    except Exception:
        return {"free_gb": 0.0, "total_gb": 0.0}


def classify_tier(gpus: list[dict], ram_gb: float = 0.0) -> str:
    """T1 <= 8 Go | T2 <= 20 Go | T3 au-dela. Sans GPU : T1.
    LOCALAI_FORCE_TIER=T1|T2|T3 force le palier (machine exotique ou test)."""
    force = os.environ.get("LOCALAI_FORCE_TIER", "").upper()
    if force in MODEL_PLANS:
        return force
    if not gpus:
        return "T1"
    vram = max((g.get("vram_gb", 0.0) for g in gpus), default=0.0)
    if vram >= 22:
        return "T3"
    if vram >= 10:
        return "T2"
    return "T1"


MODELES_OPTIONNELS: dict[str, list[str]] = {
    # MoE A3B = ~30B params, ~3B actifs -> vitesse dense 3B, qualite 30B
    "T1": ["glm4:9b", "deepseek-r1:8b", "gemma3:4b", "phi4-mini",
           "qwen3:30b-a3b"],
    "T2": ["glm4:9b", "deepseek-r1:14b", "phi4", "mistral-small:24b",
           "qwen3:30b-a3b", "qwen2.5-coder:32b"],
    "T3": ["glm4:32b", "deepseek-r1:32b", "mistral-small:24b", "qwen3:30b", "phi4",
           "qwen3:30b-a3b", "qwen2.5-coder:32b", "nemotron-nano-30b-a3b"],
}


def modeles_optionnels(tier: str) -> list[str]:
    """Modeles alternatifs installables (GLM, DeepSeek-R1, Phi...).

    S'ils sont presents, ils deviennent elegibles dans leur role via le plan.
    """
    return MODELES_OPTIONNELS.get(tier, MODELES_OPTIONNELS["T1"])


def resolve_plan(tier: str, installed_tags: list[str]) -> dict:
    """Pour chaque role, le premier modele du plan reellement installe."""
    plan_brut = MODEL_PLANS.get(tier, MODEL_PLANS["T1"])
    tags_norm = {t.lower(): t for t in installed_tags}
    resolu: dict[str, str] = {}
    manquants: dict[str, list[str]] = {}
    for role, prefs in plan_brut.items():
        for pref in prefs:
            if pref.lower() in tags_norm:
                resolu[role] = tags_norm[pref.lower()]
                break
        else:
            manquants[role] = prefs
    return {"resolu": resolu, "manquants": manquants}


def build_profile(installed_tags: list[str] | None = None) -> dict:
    """Detecte le materiel, classifie, sauvegarde data/hardware_profile.json."""
    gpus = detect_gpu()
    ram = detect_ram_gb()
    tier = classify_tier(gpus, ram)
    plan = (
        resolve_plan(tier, installed_tags)
        if installed_tags is not None
        else {"resolu": {}, "manquants": {}}
    )
    profile = {
        "detected_at": datetime.now(timezone.utc).isoformat(),
        "gpus": gpus,
        "ram_gb": ram,
        "disk": detect_disk_gb(),
        "tier": tier,
        "plan": plan,
        "cpu_only": not gpus,
    }
    try:
        PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
        PROFILE_PATH.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    return profile


def recommended_pulls(tier: str) -> list[str]:
    """Liste dedoubee des modeles a installer pour ce palier."""
    plan = MODEL_PLANS.get(tier, MODEL_PLANS["T1"])
    vues: set[str] = set()
    for prefs in plan.values():
        for pref in prefs:
            vues.add(pref)
    return sorted(vues)


def load_cached_profile() -> dict | None:
    try:
        return json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None


def get_role_model(role: str) -> str | None:
    """Modele resolu pour un role, d'apres le profil en cache (sans IO reseau)."""
    profile = load_cached_profile()
    if not profile:
        return None
    return profile.get("plan", {}).get("resolu", {}).get(role)
