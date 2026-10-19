import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from backend.api.schemas import InstallRequest, TagRequest, TagsRequest
from backend.core.catalog import build_catalog
from backend.inference.base import InstalledModel, LoadedModel
from backend.inference import get_active_backend
from backend.inference.ollama import OllamaError

router = APIRouter(prefix="/api/models", tags=["models"])


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.get("")
async def list_models() -> dict:
    backend = get_active_backend()
    installed: list[InstalledModel] = await backend.list_installed()
    loaded: list[LoadedModel] = await backend.list_loaded()
    catalog = await build_catalog()
    by_tag = {m.tag: m for m in installed}
    models = []
    seen_tags: set[str] = set()
    for entry in catalog:
        inst = by_tag.get(entry.tag)
        seen_tags.add(entry.tag)
        models.append(
            {
                "id": entry.id,
                "name": entry.name,
                "tag": entry.tag,
                "family": entry.family,
                "capabilities": entry.capabilities,
                "size_label": entry.size_label,
                "quantization": entry.quantization,
                "vram_gb": entry.vram_gb,
                "context": entry.context,
                "description": entry.description,
                "source": entry.source,
                "installed": inst is not None,
                "loaded": any(l.model == entry.tag for l in loaded),
                "size_bytes": inst.size_bytes if inst else 0,
                "parameter_size": inst.parameter_size if inst else "",
            }
        )
    for inst in installed:
        if inst.tag in seen_tags:
            continue
        models.append(
            {
                "id": inst.tag,
                "name": inst.tag.split(":")[0],
                "tag": inst.tag,
                "family": inst.family,
                "capabilities": [],
                "size_label": inst.parameter_size,
                "quantization": inst.quantization_level,
                "vram_gb": 0.0,
                "context": 0,
                "description": "",
                "source": "installed",
                "installed": True,
                "loaded": any(l.model == inst.tag for l in loaded),
                "size_bytes": inst.size_bytes,
                "parameter_size": inst.parameter_size,
            }
        )
    models.sort(key=lambda m: (not m["installed"], m["name"].lower()))
    return {"models": models}


@router.get("/installed")
async def list_installed() -> dict:
    backend = get_active_backend()
    return {"models": [vars(m) for m in await backend.list_installed()]}


@router.post("/install")
async def install_model(req: InstallRequest) -> StreamingResponse:
    backend = get_active_backend()

    async def stream() -> AsyncIterator[str]:
        try:
            async for prog in backend.pull_stream(req.tag):
                yield _sse(
                    {
                        "type": "done" if prog.done else "progress",
                        "tag": req.tag,
                        "status": prog.status,
                        "percent": prog.percent,
                        "completed": prog.completed,
                        "total": prog.total,
                        "error": prog.error,
                    }
                )
        except OllamaError as exc:
            yield _sse({"type": "error", "tag": req.tag, "error": str(exc)})

    return StreamingResponse(stream(), media_type="text/event-stream")


@router.post("/update")
async def update_models(req: TagsRequest) -> StreamingResponse:
    backend = get_active_backend()
    tags = req.tags or [m.tag for m in await backend.list_installed()]

    async def stream() -> AsyncIterator[str]:
        updated = 0
        failed: list[dict] = []
        for tag in tags:
            yield _sse({"type": "model_start", "tag": tag})
            ok = True
            error = ""
            try:
                async for prog in backend.pull_stream(tag):
                    if prog.error:
                        ok = False
                        error = prog.error
                    yield _sse(
                        {
                            "type": "progress",
                            "tag": tag,
                            "status": prog.status,
                            "percent": prog.percent,
                        }
                    )
                    if prog.done and not prog.error:
                        break
            except OllamaError as exc:
                ok = False
                error = str(exc)
            if ok:
                updated += 1
            else:
                failed.append({"tag": tag, "error": error})
            yield _sse(
                {"type": "model_done", "tag": tag, "ok": ok, "error": error}
            )
        yield _sse({"type": "done", "updated": updated, "failed": failed})

    return StreamingResponse(stream(), media_type="text/event-stream")


@router.delete("/{tag:path}")
async def delete_model(tag: str) -> dict:
    backend = get_active_backend()
    try:
        await backend.delete(tag)
    except OllamaError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"deleted": tag}


@router.post("/load")
async def load_model(req: TagRequest) -> dict:
    backend = get_active_backend()
    try:
        await backend.load(req.tag)
    except OllamaError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"loaded": req.tag}


@router.post("/unload")
async def unload_model(req: TagRequest) -> dict:
    backend = get_active_backend()
    try:
        await backend.unload(req.tag)
    except OllamaError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"unloaded": req.tag}
