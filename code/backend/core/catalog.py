import json
from dataclasses import dataclass, field

import httpx

from backend.core.config import settings


@dataclass
class CatalogEntry:
    id: str
    name: str
    tag: str
    family: str = ""
    capabilities: list[str] = field(default_factory=list)
    size_label: str = ""
    quantization: str = ""
    vram_gb: float = 0.0
    context: int = 0
    description: str = ""
    source: str = "local"


def _entry_from_dict(d: dict, source: str) -> CatalogEntry:
    return CatalogEntry(
        id=d.get("id") or d.get("tag", ""),
        name=d.get("name", d.get("tag", "")),
        tag=d.get("tag", ""),
        family=d.get("family", ""),
        capabilities=list(d.get("capabilities", [])),
        size_label=d.get("size_label", ""),
        quantization=d.get("quantization", ""),
        vram_gb=float(d.get("vram_gb", 0)),
        context=int(d.get("context", 0)),
        description=d.get("description", ""),
        source=source,
    )


def load_local_catalog() -> list[CatalogEntry]:
    if not settings.models_catalog_file.exists():
        return []
    data = json.loads(settings.models_catalog_file.read_text(encoding="utf-8-sig"))
    return [_entry_from_dict(e, "local") for e in data.get("models", [])]


async def fetch_remote_catalog() -> list[CatalogEntry]:
    if not settings.remote_catalog_url:
        return []
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(settings.remote_catalog_url)
            resp.raise_for_status()
            data = resp.json()
        return [_entry_from_dict(e, "remote") for e in data.get("models", [])]
    except (httpx.HTTPError, ValueError):
        return []


async def build_catalog() -> list[CatalogEntry]:
    entries: dict[str, CatalogEntry] = {e.tag: e for e in load_local_catalog()}
    for entry in await fetch_remote_catalog():
        if entry.tag and entry.tag not in entries:
            entries[entry.tag] = entry
    return sorted(entries.values(), key=lambda e: e.name.lower())
