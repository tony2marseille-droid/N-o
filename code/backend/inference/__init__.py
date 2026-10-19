from backend.inference.base import InferenceBackend
from backend.inference.llamacpp import LlamaCppBackend, get_llamacpp_backend
from backend.inference.ollama import OllamaBackend, OllamaError, get_backend, shutdown_backends

__all__ = [
    "InferenceBackend",
    "OllamaBackend",
    "OllamaError",
    "LlamaCppBackend",
    "get_backend",
    "get_active_backend",
    "shutdown_all_backends",
]


def get_active_backend():
    """Fabrique du backend courant : réglage BD > variable d'environnement."""
    from backend.core.config import settings

    try:
        from backend.api.routes_settings import get_setting

        chosen = get_setting("inference_backend", "") or settings.inference_backend
    except Exception:
        chosen = settings.inference_backend
    if chosen == "llamacpp":
        return get_llamacpp_backend()
    return get_backend()


async def shutdown_all_backends() -> None:
    await shutdown_backends()
    get_llamacpp_backend().stop_server()
