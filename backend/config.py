"""WealthSail application configuration (Pydantic BaseSettings).

All secrets come from the environment (or a git-ignored `.env` file). The
values below are LOCAL-DEV fallbacks only — production deploys must supply
every variable via env files (see docs/10 §8.1 api.env / worker.env).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

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
    app_env: Literal["development", "test", "staging", "production"] = Field(
        default="development",
        description="development | test | staging | production",
    )
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
    refresh_token_hmac_key: str = Field(default="dev-refresh-hmac-key-change-me")
    refresh_token_ttl_days: int = Field(default=30, ge=1, le=90)
    refresh_cookie_name: str = Field(default="wealthsail_refresh")
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
    wecom_credential_version: str = Field(default="v1")
    # 智能机器人（API 模式·长连接）是默认关闭的隔离试验通道；MVP 的
    # send_message 只使用自建应用，禁止在结果未知时自动跨通道回退。
    wecom_bot_id: str = Field(default="")
    wecom_bot_secret: str = Field(default="")
    wecom_bot_enabled: bool = Field(default=False)
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
    outbox_lease_seconds: int = Field(default=60)
    outbox_poll_interval_seconds: float = Field(default=15.0)
    outbox_delivery_concurrency: int = Field(default=4, ge=1, le=20)
    scheduler_tick_seconds: float = Field(default=20.0)

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


_DEVELOPMENT_SECRET_MARKERS = ("dev-", "dev_", "change-me", "demo")


def _is_development_secret(value: str | None) -> bool:
    normalized = (value or "").strip().lower()
    return not normalized or any(marker in normalized for marker in _DEVELOPMENT_SECRET_MARKERS)


def validate_runtime_settings(settings: Settings, *, process: str) -> Settings:
    """Fail closed when a production process receives the wrong authority.

    Compose/secret stores remain responsible for injecting values.  This
    function enforces the process boundary at runtime so a shared or stale env
    file cannot silently give API and Worker each other's credentials.
    """
    if process not in {"api", "worker"}:
        raise ValueError(f"unknown process type: {process}")
    if not settings.is_production:
        return settings
    if settings.auth_dev_mode:
        raise ValueError("AUTH_DEV_MODE must be false in production")

    if process == "api":
        if _is_development_secret(settings.jwt_secret):
            raise ValueError("JWT_SECRET must be a production secret")
        if _is_development_secret(settings.refresh_token_hmac_key):
            raise ValueError("REFRESH_TOKEN_HMAC_KEY must be a production secret")
        if not settings.database_url.strip():
            raise ValueError("DATABASE_URL is required for the API")
        if _is_development_secret(settings.binding_hmac_key):
            raise ValueError("BINDING_HMAC_KEY must be a production secret")
        if _is_development_secret(settings.routing_encryption_key):
            raise ValueError("ROUTING_ENCRYPTION_KEY must be a production secret")
        if _is_development_secret(settings.wecom_callback_token):
            raise ValueError("WECOM_CALLBACK_TOKEN must be a production secret")
        if settings.worker_database_url.strip():
            raise ValueError("WORKER_DATABASE_URL must not be provided to the API")
        if settings.wecom_corp_secret or settings.wecom_bot_secret or settings.model_api_key:
            raise ValueError("Worker/model sending credentials must not be provided to the API")
    else:
        if settings.jwt_secret.strip():
            raise ValueError("JWT_SECRET must not be provided to the Worker")
        if settings.database_url.strip():
            raise ValueError("DATABASE_URL must not be provided to the Worker")
        if not settings.worker_database_url.strip():
            raise ValueError("WORKER_DATABASE_URL is required for the Worker")
        if _is_development_secret(settings.routing_encryption_key):
            raise ValueError("ROUTING_ENCRYPTION_KEY must be a production secret")
        if not settings.wecom_corp_id.strip() or not settings.wecom_corp_secret.strip():
            raise ValueError("WECOM_CORP_ID and WECOM_CORP_SECRET are required for the Worker")
        if settings.wecom_bot_enabled and not (
            settings.wecom_bot_id.strip() and settings.wecom_bot_secret.strip()
        ):
            raise ValueError("WECOM_BOT_ENABLED requires bot id and secret")
    return settings


@lru_cache
def get_settings() -> Settings:
    return Settings()
