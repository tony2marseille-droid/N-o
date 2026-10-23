# -*- coding: utf-8 -*-
"""Routes pour les fiches de connaissances (ville, poisson, etc.)"""
from fastapi import APIRouter, HTTPException
from backend.core.database import get_db
from pydantic import BaseModel

router = APIRouter(prefix="/api/fiches", tags=["fiches"])


class FicheOut(BaseModel):
    id: int
    titre: str
    categorie: str
    resume: str
    contenu_md: str
    image_path: str
    source: str
    created_at: str
    updated_at: str


@router.get("")
async def list_fiches() -> dict:
    db = get_db()
    rows = db.query_all("SELECT * FROM fiches ORDER BY updated_at DESC")
    return {"fiches": rows}


@router.get("/{fiche_id}")
async def get_fiche(fiche_id: int) -> dict:
    db = get_db()
    row = db.query_one("SELECT * FROM fiches WHERE id=?", (fiche_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Fiche non trouvee")
    return dict(row)


@router.delete("/{fiche_id}")
async def delete_fiche(fiche_id: int) -> dict:
    db = get_db()
    db.execute("DELETE FROM fiches WHERE id=?", (fiche_id,))
    return {"deleted": fiche_id}