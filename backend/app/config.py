"""Runtime configuration. All values come from environment variables (see .env.example)."""
from __future__ import annotations

import secrets
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", env_prefix="VR_", extra="ignore")

    env: str = "development"  # development | production | test
    database_url: str = f"sqlite:///{(BACKEND_DIR / 'data' / 'voicerep.db').as_posix()}"
    data_dir: Path = BACKEND_DIR / "data"
    models_dir: Path = BACKEND_DIR / "data" / "models"

    # Master key used to derive the secret-encryption key (Fernet). Must be set in production.
    secret_key: str = ""

    # Origins allowed to open browser sessions / websockets.
    allowed_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    public_app_url: str = "http://localhost:3000"
    cookie_secure: bool = False
    session_days: int = 14

    # Background workers (reminders, push outbox, retention). Disabled in unit tests.
    run_workers: bool = True
    worker_interval_seconds: float = 10.0

    # Speech models
    asr_model: str = "base.en"  # tiny.en | base.en | small.en
    asr_threads: int = 0  # 0 = number of CPU cores
    tts_threads: int = 4
    # Download the default speech models (Whisper + Kokoro, ~0.5 GB) in the background at start-up when they
    # are missing. Off by default so local development and tests never download anything; docker-compose
    # turns it on for deployments.
    auto_install_models: bool = False

    # Optional local LLM served by llama.cpp's llama-server (OpenAI-compatible HTTP API).
    llm_url: str = ""  # e.g. http://127.0.0.1:8081
    llm_timeout_seconds: float = 4.0

    # Telephony: Asterisk AudioSocket listener
    audiosocket_enabled: bool = True
    audiosocket_host: str = "0.0.0.0"
    audiosocket_port: int = 9092
    # Shared with Asterisk, which sends it on every call. Calls are refused until this is set.
    asterisk_token: str = ""
    # Calls beyond this are forwarded or refused rather than answered too slowly. 4 is a cautious guess for now:
    # each call needs speech recognition and voice generation, so set this from a load test on the real server.
    max_concurrent_calls: int = 4
    # How long a restart waits for calls in progress to finish.
    drain_seconds: float = 120.0

    # Optional e-mail delivery (SMTP). Leave empty to disable e-mail reminders.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_starttls: bool = True

    # Web Push contact (VAPID "sub" claim)
    vapid_contact: str = "mailto:admin@example.com"

    @property
    def origins(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.data_dir.mkdir(parents=True, exist_ok=True)
    s.models_dir.mkdir(parents=True, exist_ok=True)
    if not s.secret_key:
        if s.env == "production":
            raise RuntimeError("VR_SECRET_KEY must be set in production")
        # Development convenience: persist a generated key in the data directory.
        key_file = s.data_dir / ".secret_key"
        if not key_file.exists():
            key_file.write_text(secrets.token_urlsafe(48))
        s.secret_key = key_file.read_text().strip()
    return s
