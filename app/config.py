"""Runtime configuration from environment variables (optionally a local .env file)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

APP_VERSION = "1.0.0"
ROOT_DIR = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT_DIR / "web"
SEED_CSV = Path(__file__).resolve().parent / "seed" / "food-seed-de.csv"


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader for local development. Never overrides real env vars."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


@dataclass
class Settings:
    data_dir: Path
    gemini_api_key: str | None
    gemini_model: str
    parser: str  # auto | gemini | ollama | mock | none
    ollama_url: str
    ollama_model: str
    app_pin: str | None
    max_upload_mb: int

    @property
    def db_path(self) -> Path:
        return self.data_dir / "flatfood.db"

    @property
    def receipts_dir(self) -> Path:
        return self.data_dir / "receipts"


def load_settings() -> Settings:
    _load_dotenv(Path.cwd() / ".env")
    _load_dotenv(ROOT_DIR / ".env")
    data_dir = Path(os.environ.get("DATA_DIR") or (ROOT_DIR / "data"))
    return Settings(
        data_dir=data_dir,
        gemini_api_key=(os.environ.get("GEMINI_API_KEY") or None),
        gemini_model=os.environ.get("GEMINI_MODEL") or "gemini-3.8-flash",
        parser=(os.environ.get("PARSER") or "auto").lower(),
        ollama_url=os.environ.get("OLLAMA_URL") or "http://localhost:11434",
        ollama_model=os.environ.get("OLLAMA_MODEL") or "qwen2.5vl:7b",
        app_pin=(os.environ.get("APP_PIN") or None),
        max_upload_mb=int(os.environ.get("MAX_UPLOAD_MB") or 15),
    )
