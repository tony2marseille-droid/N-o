from fastapi import APIRouter
from pydantic import BaseModel

from backend.core import security

router = APIRouter(prefix="/api/security", tags=["security"])


@router.get("")
async def security_status() -> dict:
    regles = security.charger_regles()
    return {
        "home": str(security.HOME()),
        "dossiers_autorises": security.dossiers_autorises(),
        "regles_version": regles.get("version"),
        "require_permission_outside_home": regles.get(
            "require_permission_outside_home", True
        ),
        "stockage": security.usage_disque(),
        "journal_taille": len(security.lire_journal(limite=10000)),
    }


class FolderRequest(BaseModel):
    path: str


@router.post("/folders")
async def grant_folder(req: FolderRequest) -> dict:
    try:
        liste = security.accorder_dossier(req.path)
    except FileNotFoundError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "dossiers_autorises": liste}


@router.delete("/folders")
async def revoke_folder(req: FolderRequest) -> dict:
    return {"ok": True, "dossiers_autorises": security.retirer_dossier(req.path)}


@router.get("/rules")
async def get_rules() -> dict:
    return security.charger_regles()


@router.put("/rules")
async def put_rules(regles: dict) -> dict:
    """Mise a jour du pack de regles (style signatures antivirus)."""
    sauvegardees = security.sauvegarder_regles(regles)
    security.journaliser("rules_updated", {"version": sauvegardees.get("version")})
    return {"ok": True, "rules": sauvegardees}


@router.get("/log")
async def get_log(limite: int = 100) -> dict:
    return {"events": security.lire_journal(max(1, min(limite, 1000)))}
