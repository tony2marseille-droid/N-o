"""Auto-adaptation COMPLETE des parametres au materiel detecte.

A l'installation (et a chaque demarrage si le materiel change), tous les
parametres operatoires sont recalcules d'apres le palier T1/T2/T3 :
contexte, tours d'outils, parallelisme, timeouts, quota disque propose,
mode reflexion par defaut, faces max des meshes, etc.

L'utilisateur garde la main : toute valeur qu'il surcharge dans
app_settings (cle auto_params) n'est plus ecrasee automatiquement.
"""

import json

from backend.core.database import get_db

KEY = "auto_params"
OVERRIDES_KEY = "auto_params_user"

PARAMS_PAR_PALIER: dict[str, dict] = {
    "T1": {
        "ctx_chat": 8192,
        "ctx_agent": 8192,
        "num_predict_max": 2048,
        "keep_alive_min": 30,
        "num_parallel": 1,
        "max_tool_rounds": 14,
        "command_timeout_s": 30,
        "mesh_max_faces": 30000,
        "tsr_device": "cpu",
        "reflection_default": False,
        "quota_propose_gb": 20,
        "vision_timeout_s": 120,
        "rag_rerank": False,
        "rag_multi_query": False,
    },
    "T2": {
        "ctx_chat": 16384,
        "ctx_agent": 16384,
        "num_predict_max": 4096,
        "keep_alive_min": 60,
        "num_parallel": 2,
        "max_tool_rounds": 18,
        "command_timeout_s": 60,
        "mesh_max_faces": 50000,
        "tsr_device": "cuda",
        "reflection_default": True,
        "quota_propose_gb": 60,
        "vision_timeout_s": 90,
        "rag_rerank": True,
        "rag_multi_query": True,
    },
    "T3": {
        "ctx_chat": 32768,
        "ctx_agent": 32768,
        "num_predict_max": 8192,
        "keep_alive_min": 120,
        "num_parallel": 3,
        "max_tool_rounds": 24,
        "command_timeout_s": 120,
        "mesh_max_faces": 80000,
        "tsr_device": "cuda",
        "reflection_default": True,
        "quota_propose_gb": 150,
        "vision_timeout_s": 60,
        "rag_rerank": True,
        "rag_multi_query": True,
    },
}


def _proposer_quota(free_gb: float) -> int:
    """Quota propose selon l'espace libre du disque d'installation."""
    if free_gb <= 0:
        return 10
    if free_gb < 50:
        return 10
    if free_gb < 150:
        return 40
    if free_gb < 500:
        return 100
    if free_gb < 2000:
        return 300
    return 600


def appliquer(tier: str, disk_free_gb: float = 0.0) -> dict:
    """Calcule et persiste les params du palier. Respecte les surcharges user."""
    db = get_db()
    params = dict(PARAMS_PAR_PALIER.get(tier, PARAMS_PAR_PALIER["T1"]))
    params["tier"] = tier
    if disk_free_gb > 0:
        params["quota_propose_gb"] = _proposer_quota(disk_free_gb)

    row = db.query_one("SELECT value FROM app_settings WHERE key=?", (OVERRIDES_KEY,))
    surcharges: dict = {}
    if row:
        try:
            surcharges = json.loads(row["value"])
        except Exception:
            surcharges = {}
    for k, v in surcharges.items():
        params[k] = v
    params["user_overrides"] = sorted(surcharges.keys())

    db.execute(
        f"INSERT INTO app_settings (key, value) VALUES ('{KEY}', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (json.dumps(params, ensure_ascii=False),),
    )
    return params


def get_param(cle: str, defaut=None):
    row = get_db().query_one("SELECT value FROM app_settings WHERE key=?", (KEY,))
    if not row:
        return defaut
    try:
        return json.loads(row["value"]).get(cle, defaut)
    except Exception:
        return defaut


def surcharger(cle: str, valeur) -> dict:
    """Surcharge manuelle d'un parametre (plus jamais ecrase par l'auto)."""
    db = get_db()
    row = db.query_one("SELECT value FROM app_settings WHERE key=?", (OVERRIDES_KEY,))
    surcharges: dict = {}
    if row:
        try:
            surcharges = json.loads(row["value"])
        except Exception:
            surcharges = {}
    surcharges[cle] = valeur
    db.execute(
        f"INSERT INTO app_settings (key, value) VALUES ('{OVERRIDES_KEY}', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (json.dumps(surcharges, ensure_ascii=False),),
    )
    from backend.core.hardware import load_cached_profile

    tier = (load_cached_profile() or {}).get("tier", "T1")
    disk = (load_cached_profile() or {}).get("disk", {}).get("free_gb", 0.0)
    return appliquer(tier, disk)
