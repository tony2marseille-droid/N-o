from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse
from pydantic import BaseModel

router = APIRouter(prefix="/api/sd", tags=["sd"])


@router.get("/status")
async def sd_status() -> dict:
    from backend.core.sdgen import status

    return status()


@router.post("/install")
async def sd_install(binaire_seulement: bool = False) -> dict:
    """Telecharge le binaire stable-diffusion.cpp + modele SD1.5 GGUF."""
    import asyncio

    from backend.core.sdgen import installer

    try:
        resultat = await asyncio.to_thread(installer, binaire_seulement)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    return resultat


class GenRequest(BaseModel):
    prompt: str
    negatif: str = ""
    largeur: int = 512
    hauteur: int = 512
    steps: int = 20
    graine: int = -1


@router.post("/generate")
async def sd_generate(req: GenRequest) -> dict:
    import time

    from backend.core.sdgen import generer

    sortie = str(Path("data") / f"img_gen_{int(time.time())}.png")
    message = await _gen_async(
        req.prompt, sortie, req.negatif, req.largeur, req.hauteur, req.steps, req.graine
    )
    chemin = sortie if Path(sortie).exists() else ""
    return {
        "ok": Path(sortie).exists(),
        "message": message,
        "path": chemin,
        "url": f"/api/sd/file?path={chemin}" if chemin else "",
    }


async def _gen_async(prompt, sortie, negatif, largeur, hauteur, steps, graine):
    import asyncio

    from backend.core.sdgen import generer

    return await asyncio.to_thread(
        generer, prompt, sortie, negatif, largeur, hauteur, steps, graine
    )


class ImageTo3DRequest(BaseModel):
    image_path: str
    output_stl: str = ""
    height_mm: float = 80.0
    max_faces: int = 0


@router.post("/image-to-3d")
async def image_to_3d(req: ImageTo3DRequest) -> dict:
    """Image -> mesh 3D (TripoSR local, equivalent Meshy auto-heberge)."""
    import asyncio

    from backend.tools.executor import tool_generate_mesh_from_image

    sortie = req.output_stl or str(
        Path(req.image_path).with_suffix(".stl")
    )
    message = await asyncio.to_thread(
        tool_generate_mesh_from_image,
        req.image_path,
        sortie,
        req.height_mm,
        req.max_faces or 0,
    )
    return {"ok": Path(sortie).exists() if sortie else False, "message": message, "path": sortie}


@router.get("/file")
async def sd_file(path: str):
    """Sert un PNG genere (dossier data ou autorise uniquement)."""
    from backend.core.security import verifier_acces

    p = Path(path).resolve()
    refus = verifier_acces(str(p))
    if refus or not p.exists() or p.suffix.lower() != ".png":
        return {"error": "Fichier indisponible"}
    return FileResponse(p)
