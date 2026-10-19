import asyncio
import json
import re
import subprocess
import time
from collections.abc import AsyncIterator
from pathlib import Path

import httpx

from backend.core.config import settings
from backend.inference.base import (
    BackendInfo,
    GenerationStats,
    InstalledModel,
    LoadedModel,
    PullProgress,
    StreamEvent,
)
from backend.inference.ollama import OllamaError


class LlamaCppBackend:
    """Backend local via llama-server (llama.cpp), API OpenAI compatible."""

    name = "llamacpp"

    def __init__(self):
        self._process: subprocess.Popen | None = None
        self._loaded_file: str = ""
        self._client: httpx.AsyncClient | None = None
        self._download_task: asyncio.Task | None = None

    @property
    def server_url(self) -> str:
        return f"http://127.0.0.1:{settings.llamacpp_port}"

    @property
    def server_bin(self) -> Path:
        return settings.llamacpp_server_bin

    def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(base_url=self.server_url, timeout=None)
        return self._client

    async def close(self) -> None:
        self.stop_server()
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    # ---------- gestion du process llama-server ----------

    def _is_alive(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def stop_server(self) -> None:
        if self._is_alive():
            try:
                self._process.terminate()
                try:
                    self._process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self._process.kill()
            except OSError:
                pass
        self._process = None
        self._loaded_file = ""

    async def start_server(self, gguf_filename: str, ctx_size: int = 8192) -> None:
        if not self.server_bin.exists():
            raise OllamaError(
                "llama-server introuvable. Installez-le depuis Reglages > Moteur."
            )
        model_path = settings.gguf_dir / gguf_filename
        if not model_path.exists():
            raise OllamaError(f"Fichier GGUF introuvable: {model_path}")
        self.stop_server()
        cmd = [
            str(self.server_bin),
            "--host", "127.0.0.1",
            "--port", str(settings.llamacpp_port),
            "-m", str(model_path),
            "-c", str(ctx_size),
            "-ngl", "99",
        ]
        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except OSError as exc:
            raise OllamaError(f"Demarrage llama-server impossible: {exc}") from exc
        self._loaded_file = gguf_filename
        deadline = time.monotonic() + 600
        async with httpx.AsyncClient(timeout=4) as probe:
            while time.monotonic() < deadline:
                if not self._is_alive():
                    raise OllamaError("llama-server s'est arrete pendant le chargement")
                try:
                    resp = await probe.get(f"{self.server_url}/health")
                    if resp.status_code == 200:
                        return
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(1.5)
        raise OllamaError("Chargement du modele trop long (>10 min)")

    # ---------- interface InferenceBackend ----------

    async def info(self) -> BackendInfo:
        if not self.server_bin.exists():
            return BackendInfo(name=self.name, available=False, detail="binaire absent")
        if not self._is_alive():
            return BackendInfo(name=self.name, available=False, detail="serveur arrete")
        try:
            resp = await self.client().get("/health", timeout=5)
            return BackendInfo(name=self.name, available=resp.status_code == 200, version="llama.cpp")
        except httpx.HTTPError as exc:
            return BackendInfo(name=self.name, available=False, detail=str(exc))

    async def list_installed(self) -> list[InstalledModel]:
        models = []
        for f in sorted(settings.gguf_dir.glob("*.gguf")):
            size_match = re.search(r"(\d+(?:\.\d+)?)\s*b\b", f.stem.lower())
            param = f"{size_match.group(1)}B" if size_match else ""
            quant = ""
            q_match = re.search(r"\b(IQ?\d+_[A-Z0-9]+|Q\d+_[K_S_M_L]+|Q\d+)\b", f.stem.upper())
            if q_match:
                quant = q_match.group(1)
            models.append(
                InstalledModel(
                    name=f.stem,
                    tag=f"gguf:{f.name}",
                    size_bytes=f.stat().st_size,
                    family="gguf",
                    parameter_size=param,
                    quantization_level=quant,
                    modified_at=time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(f.stat().st_mtime)),
                )
            )
        return models

    async def list_loaded(self) -> list[LoadedModel]:
        if self._is_alive() and self._loaded_file:
            info = await self.info()
            if info.available:
                return [LoadedModel(name=self._loaded_file, model=f"gguf:{self._loaded_file}")]
        return []

    async def pull_stream(self, url: str) -> AsyncIterator[PullProgress]:
        """Telecharge un GGUF (URL directe) dans gguf_dir avec progression."""
        filename = url.split("?")[0].rstrip("/").split("/")[-1]
        if not filename.lower().endswith(".gguf"):
            yield PullProgress(status="error", done=True, error="L'URL doit pointer vers un fichier .gguf")
            return
        dest = settings.gguf_dir / filename
        temp = dest.with_suffix(dest.suffix + ".part")
        try:
            async with httpx.AsyncClient(timeout=None, follow_redirects=True) as client:
                async with client.stream("GET", url) as resp:
                    if resp.status_code != 200:
                        yield PullProgress(status="error", done=True, error=f"HTTP {resp.status_code}")
                        return
                    total = int(resp.headers.get("content-length", 0))
                    done_bytes = 0
                    with open(temp, "wb") as fh:
                        async for chunk in resp.aiter_bytes(1 << 20):
                            fh.write(chunk)
                            done_bytes += len(chunk)
                            percent = (done_bytes / total * 100.0) if total else 0.0
                            yield PullProgress(
                                status="downloading",
                                total=total or done_bytes,
                                completed=done_bytes,
                                percent=round(percent, 1),
                            )
            temp.replace(dest)
            yield PullProgress(status="success", done=True, percent=100.0)
        except (httpx.HTTPError, OSError) as exc:
            temp.unlink(missing_ok=True)
            yield PullProgress(status="error", done=True, error=str(exc))

    async def delete(self, tag: str) -> None:
        filename = tag.removeprefix("gguf:")
        path = (settings.gguf_dir / filename).resolve()
        if not str(path).startswith(str(settings.gguf_dir.resolve())) or not path.exists():
            raise OllamaError(f"Modele inconnu: {tag}")
        if self._loaded_file == filename:
            self.stop_server()
        path.unlink()

    async def load(self, tag: str) -> None:
        await self.start_server(tag.removeprefix("gguf:"))

    async def unload(self, tag: str) -> None:
        self.stop_server()

    async def embed_batch(self, texts: list[str], model: str = "") -> list[list[float]]:
        raise OllamaError("Embeddings indisponibles sur llama.cpp — utilisez Ollama pour le RAG")

    async def chat_stream(
        self,
        tag: str,
        messages: list[dict],
        options: dict | None = None,
        tools: list[dict] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        if not self._is_alive():
            yield StreamEvent(type="error", content="Serveur llama.cpp arrete : chargez un modele.")
            return
        temperature = 0.7
        max_tokens = -1
        if options:
            temperature = float(options.get("temperature", 0.7))
        payload = {
            "model": "local",
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        prompt_tokens = 0
        completion_tokens = 0
        started_ns = time.perf_counter_ns()
        first_token_ns = 0
        completion_started = False
        try:
            async with self.client().stream(
                "POST", "/v1/chat/completions", json=payload, timeout=None
            ) as resp:
                if resp.status_code != 200:
                    body = (await resp.aread()).decode("utf-8", "replace")
                    yield StreamEvent(type="error", content=f"HTTP {resp.status_code}: {body[:300]}")
                    return
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data_str = line[5:].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        data = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue
                    usage = data.get("usage") or {}
                    if usage:
                        prompt_tokens = int(usage.get("prompt_tokens", 0))
                        completion_tokens = int(usage.get("completion_tokens", 0))
                    choices = data.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    content = delta.get("content") or ""
                    if content:
                        if not completion_started:
                            first_token_ns = time.perf_counter_ns()
                            completion_started = True
                        completion_tokens += 1
                        yield StreamEvent(type="token", content=content)
        except httpx.HTTPError as exc:
            yield StreamEvent(type="error", content=str(exc))
            return
        elapsed_s = max((time.perf_counter_ns() - first_token_ns) / 1e9, 1e-6) if completion_started else 1e-6
        stats = GenerationStats(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_duration_ms=int((time.perf_counter_ns() - started_ns) / 1e6),
            tokens_per_second=round(completion_tokens / elapsed_s, 1) if completion_started else 0.0,
        )
        yield StreamEvent(type="done", stats=stats)

    # ---------- divers ----------

    def is_binary_installed(self) -> bool:
        return self.server_bin.exists()


backend_registry: dict[str, object] = {}


def get_llamacpp_backend() -> LlamaCppBackend:
    if "llamacpp" not in backend_registry:
        backend_registry["llamacpp"] = LlamaCppBackend()
    return backend_registry["llamacpp"]
