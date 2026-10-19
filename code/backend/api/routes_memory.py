from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.core.database import get_db

router = APIRouter(prefix="/api/memory", tags=["memory"])


class MemoryCreate(BaseModel):
    content: str
    tag: str = "user"


@router.get("")
async def list_memories() -> dict:
    return {"memories": get_db().query_all("SELECT * FROM memories ORDER BY id DESC")}


@router.post("")
async def add_memory(req: MemoryCreate) -> dict:
    if not req.content.strip():
        raise HTTPException(status_code=422, detail="Contenu vide")
    mid = get_db().execute(
        "INSERT INTO memories (content, tag, created_at) VALUES (?,?,?)",
        (req.content.strip(), req.tag or "user", datetime.now().isoformat(timespec="seconds")),
    )
    return {"id": mid}


@router.delete("/{mid}")
async def delete_memory(mid: int) -> dict:
    get_db().execute("DELETE FROM memories WHERE id=?", (mid,))
    return {"deleted": mid}
