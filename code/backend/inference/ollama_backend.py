import json
from typing import AsyncIterator

import httpx

from backend.core.config import settings
from backend.inference.base import (
    BackendInfo,
    ChatMessage,
    GenerationStats,
    InstalledModel,
    LoadedModel,
    PullProgress,
    StreamEvent,
)


class OllamaError(Exception):
    pass


class OllamaBackend:
    name = "ollama"

    def __init__(self, host: str | None = None):
        self._host = host or settings.ollama_host
        self._client: httpx.AsyncClient | None = None

    @property
    def base_url(self) -> str:
        return self._host.rstrip("/")

    def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(base_url=self.base_url, timeout=None)
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def info(self) -> BackendInfo:
        try:
            resp = await self.client().get("/api/version", timeout=5)
            resp.raise_for_status()
            return BackendInfo(
                name=self.name,
                available=True,
                version=resp.json().get("version", ""),
            )
        except httpx.HTTPError as exc:
            return BackendInfo(name=self.name, available=False, detail=str(exc))

    async def list_installed(self) -> list[InstalledModel]:
        try:
            resp = await self.client().get("/api/tags", timeout=15)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise OllamaError(f"Ollama injoignable: {exc}") from exc
        models = []
        for m in resp.json().get("models", []):
            details = m.get("details", {}) or {}
            models.append(
                InstalledModel(
                    name=m.get("name", ""),
                    tag=m.get("model", m.get("name", "")),
                    size_bytes=int(m.get("size", 0)),
                    digest=m.get("digest", ""),
                    family=details.get("family", ""),
                    parameter_size=details.get("parameter_size", ""),
                    quantization_level=details.get("quantization_level", ""),
                    modified_at=m.get("modified_at", ""),
                )
            )
        return models

    async def list_loaded(self) -> list[LoadedModel]:
        try:
            resp = await self.client().get("/api/ps", timeout=10)
            resp.raise_for_status()
        except httpx.HTTPError:
            return []
        models = []
        for m in resp.json().get("models", []):
            models.append(
                LoadedModel(
                    name=m.get("name", ""),
                    model=m.get("model", ""),
                    size_vram=int(m.get("size_vram", 0)),
                    expires_at=m.get("expires_at", ""),
                )
            )
        return models

    async def pull_stream(self, tag: str) -> AsyncIterator[PullProgress]:
        request = {"model": tag, "stream": True}
        async with self.client().stream(
            "POST", "/api/pull", json=request, timeout=None
        ) as resp:
            if resp.status_code != 200:
                body = (await resp.aread()).decode("utf-8", "replace")
                yield PullProgress(status="error", done=True, error=body)
                return
            async for line in resp.aiter_lines():
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if "error" in data:
                    yield PullProgress(
                        status="error", done=True, error=str(data["error"])
                    )
                    return
                total = int(data.get("total", 0))
                completed = int(data.get("completed", 0))
                percent = (completed / total * 100.0) if total > 0 else 0.0
                status = data.get("status", "")
                done = status in ("success", "purging") and total == 0
                yield PullProgress(
                    status=status,
                    total=total,
                    completed=completed,
                    percent=round(percent, 1),
                    done=done,
                )

    async def delete(self, tag: str) -> None:
        resp = await self.client().request(
            "DELETE", "/api/delete", json={"model": tag}, timeout=60
        )
        if resp.status_code not in (200, 404):
            raise OllamaError(f"Suppression impossible ({resp.status_code}): {resp.text}")

    async def load(self, tag: str) -> None:
        payload = {"model": tag, "keep_alive": "30m"}
        resp = await self.client().post(
            "/api/generate", json={**payload, "prompt": "", "stream": False},
            timeout=600,
        )
        if resp.status_code != 200:
            raise OllamaError(f"Chargement impossible: {resp.text}")

    async def unload(self, tag: str) -> None:
        payload = {"model": tag, "keep_alive": 0}
        resp = await self.client().post(
            "/api/generate", json={**payload, "prompt": "", "stream": False},
            timeout=60,
        )
        if resp.status_code != 200:
            raise OllamaError(f"Déchargement impossible: {resp.text}")

    async def embed_batch(
        self, texts: list[str], model: str = "nomic-embed-text:latest"
    ) -> list[list[float]]:
        resp = await self.client().post(
            "/api/embed", json={"model": model, "input": texts}, timeout=300
        )
        if resp.status_code != 200:
            raise OllamaError(f"Embedding impossible: {resp.text[:200]}")
        return [list(e) for e in resp.json().get("embeddings", [])]

    async def chat_stream(
        self,
        tag: str,
        messages: list[dict],
        options: dict | None = None,
        tools: list[dict] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        payload = {
            "model": tag,
            "messages": messages,
            "stream": True,
            "keep_alive": "30m",
        }
        if options:
            payload["options"] = options
        if tools:
            payload["tools"] = tools
        prompt_tokens = 0
        completion_tokens = 0
        total_duration_ns = 0
        eval_duration_ns = 0
        collected_calls: list[dict] = []
        async with self.client().stream(
            "POST", "/api/chat", json=payload, timeout=None
        ) as resp:
            if resp.status_code != 200:
                body = (await resp.aread()).decode("utf-8", "replace")
                yield StreamEvent(type="error", content=f"HTTP {resp.status_code}: {body}")
                return
            async for line in resp.aiter_lines():
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if "error" in data:
                    yield StreamEvent(type="error", content=str(data["error"]))
                    return
                chunk = data.get("message", {}) or {}
                content = chunk.get("content", "")
                if content:
                    yield StreamEvent(type="token", content=content)
                calls = chunk.get("tool_calls")
                if calls:
                    collected_calls.extend(calls)
                if data.get("done"):
                    prompt_tokens = int(data.get("prompt_eval_count", 0) or 0)
                    completion_tokens = int(data.get("eval_count", 0) or 0)
                    total_duration_ns = int(data.get("total_duration", 0) or 0)
                    eval_duration_ns = int(data.get("eval_duration", 0) or 0)
        tps = 0.0
        if eval_duration_ns > 0 and completion_tokens > 0:
            tps = round(completion_tokens / (eval_duration_ns / 1e9), 1)
        stats = GenerationStats(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_duration_ms=int(total_duration_ns / 1e6),
            tokens_per_second=tps,
        )
        yield StreamEvent(
            type="done",
            stats=stats,
            tool_calls=collected_calls if collected_calls else None,
        )


backend_registry: dict[str, OllamaBackend] = {}


def get_backend(name: str = "ollama") -> OllamaBackend:
    if name not in backend_registry:
        if name != "ollama":
            raise KeyError(f"Backend inconnu: {name}")
        backend_registry[name] = OllamaBackend()
    return backend_registry[name]


async def shutdown_backends() -> None:
    for b in backend_registry.values():
        await b.close()
    backend_registry.clear()
