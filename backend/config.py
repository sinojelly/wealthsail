"""WealthSail application configuration (Pydantic BaseSettings).

All secrets come from the environment (or a git-ignored `.env` file). The
values below are LOCAL-DEV fallbacks only — production deploys must supply
every variable via env files (see docs/10 §8.1 api.env / worker.env).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Runtime -----------------------------------------------------------
    app_env: str = Field(default="development", description="development | production")
    log_level: str = Field(default="INFO")
    api_prefix: str = Field(default="/api/v1")
    timezone: str = Field(default="Asia/Shanghai")
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    # --- Database -----------------------------------------------------------
    # Local dev default targets the working local Postgres 16 instance
    # (port 5433). Supabase/remote setups override DATABASE_URL entirely.
    database_url: str = Field(
        default="postgresql://wealthsail_user:dev_password_123@localhost:5433/wealthsail_dev"
    )
    worker_database_url: str = Field(
        default="postgresql://wealthsail_worker:dev_worker_password_123@localhost:5433/wealthsail_dev"
    )
    db_pool_min_size: int = Field(default=2)
    db_pool_max_size: int = Field(default=10)
    # Transaction-pooler (PgBouncer, Supabase Session Pooler 6543) deployments
    # must set statement_cache_size=0 in the DSN query string (asyncpg reads
    # it from there). None = let the DSN decide (direct connections keep the
    # asyncpg default of 100).
    db_statement_cache_size: int | None = Field(default=None)

    # --- Auth (Supabase-compatible JWT) --------------------------------------
    jwt_secret: str = Field(default="dev-only-jwt-secret-change-me")
    jwt_algorithm: str = Field(default="HS256")
    access_token_ttl_seconds: int = Field(default=3600)
    # When true, POST /api/v1/auth/login accepts any non-empty password for the
    # seeded pilot profiles (local development only; Supabase Auth owns login in
    # production).
    auth_dev_mode: bool = Field(default=True)

    # --- DeepSeek model gateway ----------------------------------------------
    model_base_url: str = Field(default="https://api.deepseek.com/v1")
    model_api_key: str | None = Field(default=None)
    model_name: str = Field(default="deepseek-chat")
    model_timeout_seconds: float = Field(default=45.0)

    # --- WeCom callback / binding --------------------------------------------
    wecom_callback_token: str = Field(default="dev-wecom-callback-token")
    wecom_callback_aes_key: str = Field(
        default="AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8"
    )  # 43-char AES key per WeCom spec (dev placeholder)
    wecom_corp_id: str = Field(default="ww-dev-corp-id")
    wecom_corp_secret: str = Field(default="")
    wecom_agent_id: str = Field(default="1000002")
    # 智能机器人（API模式·长连接）：配置后 send_message 优先走机器人推送
    wecom_bot_id: str = Field(default="")
    wecom_bot_secret: str = Field(default="")
    # Salt/key for HMAC-SHA256 of one-time binding tokens (never store plaintext).
    binding_hmac_key: str = Field(default="dev-binding-hmac-key-change-me")
    binding_token_ttl_seconds: int = Field(default=900)
    binding_token_max_attempts: int = Field(default=5)
    # Envelope-encryption key for reversible routing values (AES-256-GCM).
    routing_encryption_key: str = Field(default="dev-routing-key-32-bytes-minimum!")

    # --- Jobs / Outbox --------------------------------------------------------
    job_lease_timeout_seconds: int = Field(default=900)
    outbox_max_attempts: int = Field(default=5)
    outbox_base_backoff_seconds: int = Field(default=2)
    outbox_poll_interval_seconds: float = Field(default=15.0)
    scheduler_tick_seconds: float = Field(default=20.0)

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
