"""Runtime configuration. Values come from environment variables or the project's .env (see .env.example)."""
from __future__ import annotations

import re
import secrets
from urllib.parse import quote
from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
# The one .env at the repository root is shared by this backend, the Next.js frontend and docker compose.
# Inside the api container that path doesn't exist, which is fine: docker compose passes the values in instead.
ENV_FILE = BACKEND_DIR.parent / ".env"


def normalize_database_url(url: str) -> str:
    """Make a pasted database address usable. Addresses copied from Supabase and similar services often have a password
    with characters like @ or $ that must be URL-encoded, name the driver without "+psycopg", and lack SSL: each of those
    makes the connection fail in a confusing way ("failed to resolve host"), so they are repaired here."""
    url = url.strip().strip("\"'")
    m = re.match(r"^(?P<scheme>[a-z][a-z0-9+]*)://(?P<user>[^:/@]+):(?P<pw>.*)@(?P<rest>[^@/]+(?:/.*)?)$", url)
    if not m or m["scheme"].startswith("sqlite"):
        return url
    scheme, pw, rest = m["scheme"], m["pw"], m["rest"]
    if scheme in ("postgres", "postgresql"):
        scheme = "postgresql+psycopg"
    if not re.fullmatch(r"[A-Za-z0-9._~%-]*", pw):  # unencoded special characters: encode the whole password
        pw = quote(pw, safe="")
    host = re.match(r"[^:/?]+", rest)[0]
    if host.endswith(".pooler.supabase.com"):
        rest = rest.replace(":6543", ":5432", 1)  # the transaction pooler breaks prepared statements: use the session pooler
    if host.endswith((".supabase.com", ".supabase.co")) and "sslmode=" not in rest:
        rest += ("&" if "?" in rest else "?") + "sslmode=require"
    return f"{scheme}://{m['user']}:{pw}@{rest}"


class Settings(BaseSettings):
    # Real environment variables win over the .env file. A key left blank (VR_LLM_URL=) keeps the default below.
    model_config = SettingsConfigDict(env_file=ENV_FILE, env_prefix="VR_", extra="ignore", env_ignore_empty=True)

    env: str = "development"  # development | production | test
    database_url: str = f"sqlite:///{(BACKEND_DIR / 'data' / 'voicerep.db').as_posix()}"
    db_pool_size: int = 10  # PostgreSQL connections kept open. Supabase's free plan allows few: use 5 with max_overflow 5
    db_max_overflow: int = 20
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
    tts_engine: str = "kokoro"  # kokoro (natural, needs a fast CPU) | piper (plainer, fast on any CPU) | fish (hosted)
    # Download the default speech models (Whisper + Kokoro, ~0.5 GB) in the background at start-up when they
    # are missing. Off by default so local development and tests never download anything; docker-compose
    # turns it on for deployments.
    auto_install_models: bool = False

    # Optional hosted services. Each is used only when its key is set, and each falls back to the local models.
    # The keys are read as DEEPGRAM_API_KEY etc. (or with the VR_ prefix). Callers' audio goes to Deepgram, the
    # words of unclear sentences to Groq, and the receptionist's replies to Fish Audio, so mention them in your
    # privacy policy (and check what your clients' rules require, e.g. HIPAA for medical offices).
    deepgram_api_key: str = Field("", validation_alias=AliasChoices("VR_DEEPGRAM_API_KEY", "DEEPGRAM_API_KEY"))
    groq_api_key: str = Field("", validation_alias=AliasChoices("VR_GROQ_API_KEY", "GROQ_API_KEY"))
    fish_audio_api_key: str = Field("", validation_alias=AliasChoices("VR_FISH_AUDIO_API_KEY", "FISH_AUDIO_API_KEY"))
    asr_provider: str = "auto"  # auto (Deepgram when its key is set) | local
    local_fallback: bool = True  # false = never load the local speech models: if a hosted service fails, the call fails
    agent_mode: str = "auto"  # auto (the LangGraph agent when a Groq key is set) | off
    llm_provider: str = "auto"  # auto (Groq when its key is set and no llm_url) | local
    groq_model: str = "qwen/qwen3.8-27b"  # about 200 ms and 85 tokens for a classification
    llm_calls_per_call: int = 6  # hard cap on LLM calls in one conversation, to bound the cost
    fish_voice_id: str = "933563129e564b19a115bedd57b7406a"  # "Sarah" (female, conversational). Without one, Fish may pick a different voice per request
    fish_monthly_chars: int = 9000  # Fish Audio's free plan is 10,000 characters a month; above this the local voice is used

    # Optional local LLM served by llama.cpp's llama-server (OpenAI-compatible HTTP API).
    llm_url: str = ""  # e.g. http://127.0.0.1:8081
    llm_timeout_seconds: float = 4.0

    # Telephony: Asterisk AudioSocket listener
    audiosocket_enabled: bool = True
    audiosocket_host: str = "0.0.0.0"
    audiosocket_port: int = 9092

    # Optional e-mail delivery (SMTP). Leave empty to disable e-mail reminders.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_starttls: bool = True

    # Web Push contact (VAPID "sub" claim)
    vapid_contact: str = "mailto:admin@example.com"

    @field_validator("database_url", mode="before")
    @classmethod
    def _repair_database_url(cls, v):
        return normalize_database_url(v) if isinstance(v, str) else v

    @property
    def origins(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    @property
    def secure_cookies(self) -> bool:
        """Session cookies are only ever sent over HTTPS in production, whatever VR_COOKIE_SECURE says."""
        return self.cookie_secure or self.env == "production"

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
