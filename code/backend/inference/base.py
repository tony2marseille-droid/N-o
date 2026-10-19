from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import AsyncIterator


@dataclass
class InstalledModel:
    name: str
    tag: str
    size_bytes: int = 0
    digest: str = ""
    family: str = ""
    parameter_size: str = ""
    quantization_level: str = ""
    modified_at: str = ""


@dataclass
class LoadedModel:
    name: str
    model: str
    size_vram: int = 0
    expires_at: str = ""


@dataclass
class ChatMessage:
    role: str
    content: str


@dataclass
class GenerationStats:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_duration_ms: int = 0
    tokens_per_second: float = 0.0


@dataclass
class StreamEvent:
    type: str
    content: str = ""
    stats: GenerationStats | None = None
    tool_calls: list | None = None


@dataclass
class PullProgress:
    status: str = ""
    total: int = 0
    completed: int = 0
    percent: float = 0.0
    done: bool = False
    error: str = ""


@dataclass
class BackendInfo:
    name: str
    available: bool
    version: str = ""
    detail: str = ""


class InferenceBackend(ABC):
    name: str = "base"

    @abstractmethod
    async def info(self) -> BackendInfo: ...

    @abstractmethod
    async def list_installed(self) -> list[InstalledModel]: ...

    @abstractmethod
    async def list_loaded(self) -> list[LoadedModel]: ...

    @abstractmethod
    def pull_stream(self, tag: str) -> AsyncIterator[PullProgress]: ...

    @abstractmethod
    async def delete(self, tag: str) -> None: ...

    @abstractmethod
    async def load(self, tag: str) -> None: ...

    @abstractmethod
    async def unload(self, tag: str) -> None: ...

    @abstractmethod
    def chat_stream(
        self,
        tag: str,
        messages: list[ChatMessage],
        options: dict | None = None,
    ) -> AsyncIterator[StreamEvent]:
        ...
