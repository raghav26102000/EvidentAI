"""Application settings loaded from env vars.

All secrets/URLs read from environment. Missing critical vars fail fast.
"""
from __future__ import annotations
import os
from functools import lru_cache
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


class Settings:
    def __init__(self) -> None:
        self.postgres_url: str = os.environ["POSTGRES_URL"]
        self.postgres_auth_url: str = os.environ["POSTGRES_AUTH_URL"]
        self.jwt_private_key: str = Path(os.environ["JWT_PRIVATE_KEY_PATH"]).read_text()
        self.jwt_public_key: str = Path(os.environ["JWT_PUBLIC_KEY_PATH"]).read_text()
        self.jwt_access_ttl: int = int(os.environ.get("JWT_ACCESS_TTL_SECONDS", "600"))
        self.jwt_refresh_ttl: int = int(os.environ.get("JWT_REFRESH_TTL_SECONDS", "1209600"))
        self.master_kek_b64: str = os.environ["MASTER_KEK_B64"]
        self.max_upload_bytes: int = int(os.environ.get("MAX_UPLOAD_BYTES", "52428800"))
        self.clamd_socket: str = os.environ.get("CLAMD_SOCKET", "/var/run/clamav/clamd.ctl")
        self.clamav_scan_timeout: int = int(os.environ.get("CLAMAV_SCAN_TIMEOUT", "30"))
        self.emergent_llm_key: str = os.environ.get("EMERGENT_LLM_KEY", "")
        # Phase 3: per-agent model selection. Format = "provider/model_name".
        # Providers: openai | anthropic | gemini. Change either independently
        # without touching code. If you swap the underlying LLM key later
        # (e.g. Groq/DeepSeek via a different SDK), only this file changes.
        self.insight_agent_model: str = os.environ.get(
            "INSIGHT_AGENT_MODEL", "openai/gpt-5.4"
        )
        self.critic_agent_model: str = os.environ.get(
            "CRITIC_AGENT_MODEL", "anthropic/claude-sonnet-4-6"
        )
        self.app_name: str = os.environ.get("APP_NAME", "evidentai")
        self.cookie_secure: bool = os.environ.get("COOKIE_SECURE", "true").lower() == "true"
        self.cors_origins: list[str] = [
            o.strip() for o in os.environ.get("CORS_ORIGINS", "*").split(",") if o.strip()
        ]


@lru_cache
def get_settings() -> Settings:
    return Settings()
