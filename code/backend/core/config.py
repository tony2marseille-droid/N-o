import sys
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

APP_NAME = "LocalAI"
APP_VERSION = "0.3.0"


def _default_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def _bundled() -> Path:
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return _default_root()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="LOCALAI_",
        env_file=str(_default_root() / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    host: str = "0.0.0.0"
    port: int = 8320
    ollama_host: str = "http://127.0.0.1:11434"
    inference_backend: str = "ollama"
    llamacpp_bin_dir: Path = _default_root() / "llamacpp"
    llamacpp_port: int = 8399
    gguf_dir: Path = _default_root() / "gguf"
    root_dir: Path = _default_root()
    data_dir: Path = _default_root() / "data"
    models_catalog_file: Path = _bundled() / "config" / "models.json"
    remote_catalog_url: str = ""
    update_manifest_url: str = ""
    default_model: str = ""
    tts_voice: str = ""
    tts_rate: int = 185

    @property
    def llamacpp_server_bin(self) -> Path:
        return self.llamacpp_bin_dir / "llama-server.exe"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "localai.db"

    @property
    def tts_cache_dir(self) -> Path:
        return self.data_dir / "tts"

    @property
    def frontend_dist(self) -> Path:
        bundled = _bundled() / "webui"
        if bundled.is_dir():
            return bundled
        dev = self.root_dir / "frontend" / "dist"
        if dev.is_dir():
            return dev
        return bundled


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
settings.tts_cache_dir.mkdir(parents=True, exist_ok=True)
settings.gguf_dir.mkdir(parents=True, exist_ok=True)

