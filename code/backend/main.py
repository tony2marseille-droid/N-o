import asyncio

import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.api import (
    routes_fiches,
    routes_benchmark,
    routes_chat,
    routes_collab,
    routes_documents,
    routes_extras,
    routes_finetune,
    routes_livekit,
    routes_memory,
    routes_models,
    routes_security,
    routes_sd,
    routes_settings,
    routes_style,
    routes_studio,
    routes_system,
    routes_telegram,
    routes_tts,
    routes_voice,
)
from backend.core.config import APP_NAME, APP_VERSION, settings
from backend.core.database import init_database
from backend.inference import shutdown_all_backends


async def _auto_distillation() -> None:
    """Au demarrage : si >= 20 reponses faibles non retraitees, lance un upgrade."""
    import sqlite3

    try:
        conn = sqlite3.connect(settings.db_path)
        (faibles,) = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE role='assistant' AND quality_score IS NOT NULL "
            "AND quality_score < 55"
        ).fetchone()
        conn.close()
        if faibles < 20:
            return
        from backend.core.distiller import upgrade_weak_answers
        from backend.inference import get_active_backend

        rapport = await upgrade_weak_answers(get_active_backend())
        print(f"[auto-distill] {rapport.get('traites', 0)} reponses faibles retravaillees")
    except Exception as exc:
        print(f"[auto-distill] ignore: {exc}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_database(settings.db_path)
    try:
        from backend.core.autotune import appliquer
        from backend.core.hardware import build_profile

        profil = build_profile()
        disque = profil.get("disk", {}).get("free_gb", 0.0)
        params = appliquer(profil.get("tier", "T1"), disque)
        print(
            f"[autotune] palier {params['tier']} | ctx {params['ctx_chat']} | "
            f"tours outils {params['max_tool_rounds']} | quota propose "
            f"{params['quota_propose_gb']} Go | surcharges user: "
            f"{params.get('user_overrides', [])}"
        )
    except Exception as exc:
        print(f"[autotune] defaut: {exc}")
    asyncio.create_task(_auto_distillation())
    try:
        from backend.api.routes_telegram import start_poller

        await start_poller()
    except Exception as exc:
        print(f"[telegram] poller non demarre: {exc}")
    yield
    try:
        from backend.api.routes_telegram import stop_poller

        await stop_poller()
    except Exception:
        pass
    await shutdown_all_backends()


def create_app() -> FastAPI:
    app = FastAPI(title=APP_NAME, version=APP_VERSION, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(routes_models.router)
    app.include_router(routes_chat.router)
    app.include_router(routes_system.router)
    app.include_router(routes_tts.router)
    app.include_router(routes_memory.router)
    app.include_router(routes_documents.router)
    app.include_router(routes_fiches.router)
    app.include_router(routes_benchmark.router)
    app.include_router(routes_settings.router)
    app.include_router(routes_security.router)
    app.include_router(routes_sd.router)
    app.include_router(routes_collab.router)
    app.include_router(routes_extras.router)
    app.include_router(routes_finetune.router)
    app.include_router(routes_voice.router)
    app.include_router(routes_telegram.router)
    app.include_router(routes_livekit.router)
    app.include_router(routes_style.router)
    app.include_router(routes_studio.router)

    @app.get("/api/health")
    async def health() -> dict:
        return {"status": "ok", "app": APP_NAME, "version": APP_VERSION, "build": "20261023-fix"}

    dist = settings.frontend_dist
    index_file = dist / "index.html"
    if dist.is_dir() and index_file.exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa(full_path: str):
            # Exclude API paths - let API routers handle them
            if full_path.startswith("api/"):
                from fastapi import HTTPException
                raise HTTPException(status_code=404, detail="Not found")
            candidate = dist / full_path
            if full_path and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(index_file)
    else:
        @app.get("/", include_in_schema=False)
        async def no_frontend():
            return JSONResponse(
                {"detail": "Frontend non buildÃ©. Lancez: npm run build dans frontend/"},
                status_code=404,
            )

    return app


app = create_app()


if __name__ == "__main__":
    uvicorn.run("backend.main:app", host=settings.host, port=settings.port)
