import asyncio
import json
import subprocess
import os
import time
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path

import httpx
import psutil
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from backend.core.config import APP_NAME, APP_VERSION, settings

router = APIRouter(prefix="/api", tags=["system"])

_BOOT_TIME = psutil.boot_time()


def _ollama_models_dir() -> str:
    env_dir = os.environ.get("OLLAMA_MODELS")
    if env_dir:
        return env_dir
    default = Path.home() / ".ollama" / "models"
    return str(default) if default.exists() else ""


async def _gpu_info() -> list[dict]:
    cmd = [
        "nvidia-smi",
        "--query-gpu=name,memory.used,memory.total,utilization.gpu,temperature.gpu",
        "--format=csv,noheader,nounits",
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        stdout, _ = await proc.communicate()
    except (FileNotFoundError, OSError):
        return []
    gpus = []
    for line in stdout.decode("utf-8", "replace").strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 5:
            try:
                gpus.append(
                    {
                        "name": parts[0],
                        "vram_used_mb": int(parts[1]),
                        "vram_total_mb": int(parts[2]),
                        "utilization_percent": int(float(parts[3])),
                        "temperature_c": int(float(parts[4])),
                    }
                )
            except ValueError:
                continue
    return gpus


@router.get("/system/info")
async def system_info() -> dict:
    cpu_percent = psutil.cpu_percent(interval=0.15)
    mem = psutil.virtual_memory()
    disks = []
    for part in psutil.disk_partitions():
        if "cdrom" in part.opts or not part.fstype:
            continue
        try:
            usage = psutil.disk_usage(part.mountpoint)
            disks.append(
                {
                    "mount": part.mountpoint,
                    "total_gb": round(usage.total / 1e9, 1),
                    "free_gb": round(usage.free / 1e9, 1),
                    "used_percent": round(usage.percent, 1),
                }
            )
        except PermissionError:
            continue

    from backend.inference.ollama import get_backend

    ollama_info = await get_backend().info()

    return {
        "app": {"name": APP_NAME, "version": APP_VERSION},
        "uptime_seconds": int(time.time() - _BOOT_TIME),
        "cpu": {
            "percent": cpu_percent,
            "cores_physical": psutil.cpu_count(logical=False),
            "cores_logical": psutil.cpu_count(),
        },
        "ram": {
            "used_gb": round((mem.total - mem.available) / 1e9, 2),
            "total_gb": round(mem.total / 1e9, 2),
            "percent": mem.percent,
        },
        "disks": disks,
        "gpus": await _gpu_info(),
        "ollama": {
            "available": ollama_info.available,
            "version": ollama_info.version,
            "host": settings.ollama_host,
        },
        "models_dir": _ollama_models_dir(),
        "time": datetime.now().isoformat(timespec="seconds"),
    }


@router.get("/system/hardware")
async def hardware_profile() -> dict:
    """Detection materielle + plan de modeles par role pour ce palier."""
    from backend.core.hardware import build_profile

    installed: list[str] = []
    try:
        from backend.inference.ollama import get_backend

        installed = [m.tag for m in await get_backend().list_installed()]
    except Exception:
        pass
    return build_profile(installed)


@router.get("/system/hardware/pulls")
async def hardware_recommended_pulls() -> dict:
    """Modeles a installer sur cette machine (palier detecte)."""
    from backend.core.hardware import (
        build_profile,
        modeles_optionnels,
        recommended_pulls,
    )

    profile = build_profile()
    return {
        "tier": profile["tier"],
        "pulls": recommended_pulls(profile["tier"]),
        "optionnels": modeles_optionnels(profile["tier"]),
    }


def _find_tailscale_cli() -> str | None:
    candidates = [
        os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Tailscale", "tailscale.exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Tailscale", "tailscale.exe"),
    ]
    for c in candidates:
        if c and Path(c).exists():
            return c
    return None


@router.get("/tailscale")
async def tailscale_status() -> dict:
    cli = _find_tailscale_cli()
    result = {"installed": bool(cli), "cli": cli or "", "state": "", "ip4": "", "serve_published": False}
    if not cli:
        return result

    async def run(args: str, timeout: float = 15) -> str:
        proc = await asyncio.create_subprocess_exec(
            cli, *args.split(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return ""
        return out.decode("utf-8", "replace").strip()

    try:
        status_json = await run("status --json")
        if status_json:
            data = json.loads(status_json)
            result["state"] = str(data.get("BackendState", ""))
            self_key = str(data.get("Self", {}).get("UserID", ""))
            ips = data.get("Self", {}).get("TailscaleIPs", []) or []
            if ips:
                result["ip4"] = str(ips[0])
    except (ValueError, KeyError):
        pass
    serve_out = await run("serve status")
    result["serve_published"] = ":8320" in serve_out
    return result


@router.post("/tailscale/publish")
async def tailscale_publish() -> dict:
    cli = _find_tailscale_cli()
    if not cli:
        raise HTTPException(status_code=404, detail="Tailscale non installÃ©")

    async def run(args: list[str], timeout: float = 60) -> tuple[int, str]:
        proc = await asyncio.create_subprocess_exec(
            cli, *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return -1, "timeout"
        return proc.returncode or 0, out.decode("utf-8", "replace").strip()

    code, out = await run(["up", "--timeout=30s"], timeout=45)
    if code not in (0,):
        raise HTTPException(status_code=502, detail=f"tailscale up: {out[:300]}")
    code, out = await run(["serve", "--bg", "http://127.0.0.1:8320"], timeout=60)
    if code != 0:
        raise HTTPException(status_code=502, detail=f"tailscale serve: {out[:300]}")
    code, ip_out = await run(["ip", "-4"], timeout=20)
    ip = ip_out.splitlines()[0].strip() if ip_out else ""
    return {"published": True, "url": f"http://{ip}:8320", "ip": ip}


@router.get("/app/version")
async def app_version() -> dict:
    return {"name": APP_NAME, "version": APP_VERSION}


LLAMACPP_RELEASES_API = "https://api.github.com/repos/ggml-org/llama.cpp/releases"


def _sse(data: dict) -> str:
    import json as _json

    return f"data: {_json.dumps(data, ensure_ascii=False)}\n\n"


def _pick_llamacpp_asset(assets: list[dict], variant: str) -> dict | None:
    import re as _re

    pattern = (
        r"llama-b\d+-bin-win-cuda-cu[\d.]+-x64\.zip"
        if variant == "cuda"
        else r"llama-b\d+-bin-win-cpu-x64\.zip"
    )
    for a in assets:
        name = a.get("name", "")
        if _re.fullmatch(pattern, name):
            return a
    return None


@router.get("/llamacpp/status")
async def llamacpp_status() -> dict:
    from backend.inference.llamacpp import get_llamacpp_backend

    backend = get_llamacpp_backend()
    info = await backend.info()
    try:
        from backend.api.routes_settings import get_setting

        active = get_setting("inference_backend", "") or settings.inference_backend
    except Exception:
        active = settings.inference_backend
    ggufs = [f.name for f in sorted(settings.gguf_dir.glob("*.gguf"))]
    return {
        "binary_installed": backend.is_binary_installed(),
        "bin_path": str(backend.server_bin),
        "server_running": bool(info.available),
        "detail": info.detail,
        "gguf_dir": str(settings.gguf_dir),
        "models": ggufs,
        "port": settings.llamacpp_port,
        "active_backend": active,
    }


@router.post("/llamacpp/install")
async def install_llamacpp(variant: str = "cuda") -> StreamingResponse:
    """Telecharge llama.cpp officiel (zip release) et extrait llama-server."""
    async def stream() -> AsyncIterator[str]:
        yield _sse({"type": "progress", "step": "resolution", "percent": 0})
        zip_path = settings.data_dir / "_llamacpp.zip"
        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                resp = await client.get(
                    LLAMACPP_RELEASES_API + "?per_page=10",
                    headers={"Accept": "application/vnd.github+json"},
                )
                resp.raise_for_status()
                asset = None
                for release in resp.json():
                    asset = _pick_llamacpp_asset(release.get("assets", []), variant)
                    if asset is not None:
                        break
                if asset is None:
                    yield _sse({"type": "error", "error": "Aucun asset llama.cpp Windows trouve"})
                    return
                url = asset["browser_download_url"]
                total = int(asset.get("size", 0))
                yield _sse({"type": "progress", "step": "download", "percent": 0, "total": total})
                done = 0
                async with client.stream("GET", url) as dl:
                    dl.raise_for_status()
                    if not total:
                        total = int(dl.headers.get("content-length", 0))
                    with open(zip_path, "wb") as fh:
                        async for chunk in dl.aiter_bytes(1 << 20):
                            fh.write(chunk)
                            done += len(chunk)
                            pct = (done / total * 100.0) if total else 0.0
                            yield _sse(
                                {"type": "progress", "step": "download", "percent": round(pct, 1),
                                 "completed": done, "total": total}
                            )
        except httpx.HTTPError as exc:
            zip_path.unlink(missing_ok=True)
            yield _sse({"type": "error", "error": f"Telechargement echoue: {exc}"})
            return
        yield _sse({"type": "progress", "step": "extract", "percent": 99})
        try:
            import zipfile

            dest_dir = settings.llamacpp_bin_dir
            dest_dir.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(zip_path) as zf:
                for member in zf.namelist():
                    base = member.replace("\\", "/").split("/")[-1]
                    if not base:
                        continue
                    target = dest_dir / base
                    with zf.open(member) as src, open(target, "wb") as out:
                        out.write(src.read())
        except (OSError, zipfile.BadZipFile) as exc:
            yield _sse({"type": "error", "error": f"Extraction echouee: {exc}"})
            return
        finally:
            zip_path.unlink(missing_ok=True)
        yield _sse({"type": "done", "bin": str(settings.llamacpp_server_bin)})

    return StreamingResponse(stream(), media_type="text/event-stream")


def _parse_version(v: str) -> tuple[int, ...]:
    digits = "".join(ch if ch.isdigit() or ch == "." else "" for ch in v)
    try:
        return tuple(int(p) for p in digits.split(".") if p != "")
    except ValueError:
        return (0,)


@router.get("/app/check-update")
async def check_update() -> dict:
    result: dict = {
        "current_version": APP_VERSION,
        "update_available": False,
        "latest_version": "",
        "url": "",
        "notes": "",
        "configured": bool(settings.update_manifest_url),
    }
    if not settings.update_manifest_url:
        return result
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(settings.update_manifest_url)
            resp.raise_for_status()
            manifest = resp.json()
    except (httpx.HTTPError, ValueError):
        return result
    latest = str(manifest.get("version", ""))
    result["latest_version"] = latest
    result["url"] = manifest.get("url", "")
    result["notes"] = manifest.get("notes", "")
    result["sha256"] = str(manifest.get("sha256", ""))
    result["update_available"] = _parse_version(latest) > _parse_version(APP_VERSION)
    return result


@router.post("/app/download-update")
async def download_update() -> dict:
    check = await check_update()
    if not check["update_available"] or not check["url"]:
        raise HTTPException(status_code=409, detail="Aucune mise Ã  jour disponible")
    updates_dir = settings.data_dir / "updates"
    updates_dir.mkdir(parents=True, exist_ok=True)
    filename = check["url"].split("/")[-1].split("?")[0] or "update.exe"
    dest = updates_dir / filename
    try:
        async with httpx.AsyncClient(timeout=None, follow_redirects=True) as client:
            async with client.stream("GET", check["url"]) as resp:
                resp.raise_for_status()
                with open(dest, "wb") as fh:
                    async for chunk in resp.aiter_bytes(1 << 20):
                        fh.write(chunk)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"TÃ©lÃ©chargement Ã©chouÃ©: {exc}") from exc
    if check.get("sha256"):
        import hashlib

        digest = hashlib.sha256(dest.read_bytes()).hexdigest()
        if digest != check["sha256"].lower():
            dest.unlink(missing_ok=True)
            raise HTTPException(
                status_code=502,
                detail="IntÃ©gritÃ© Ã©chouÃ©e : empreinte SHA256 diffÃ©rente du manifeste",
            )
    return {"downloaded": str(dest), "version": check["latest_version"]}
