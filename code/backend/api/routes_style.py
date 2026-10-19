import asyncio

from fastapi import APIRouter

from backend.core.style import (
    analyze_style,
    get_style_profile,
    save_style_profile,
    style_enabled,
)
from backend.inference import get_active_backend

router = APIRouter(prefix="/api/style", tags=["style"])

_analysis: asyncio.Task | None = None


@router.get("")
async def style_status() -> dict:
    return {
        "enabled": style_enabled(),
        "profile": get_style_profile(),
    }


@router.post("/refresh")
async def style_refresh() -> dict:
    global _analysis

    async def _run():
        return await analyze_style(get_active_backend())

    if _analysis is not None and not _analysis.done():
        return {"ok": False, "started": True, "detail": "analyse deja en cours"}
    _analysis = asyncio.create_task(_run())
    return {"ok": True, "started": True}


@router.post("/reset")
async def style_reset() -> dict:
    save_style_profile({})
    return {"ok": True, "profile": get_style_profile()}