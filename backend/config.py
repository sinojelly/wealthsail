"""WealthSail application configuration (Pydantic BaseSettings).

All secrets come from the environment (or a git-ignored `.env` file). The
values below are LOCAL-DEV fallbacks only — production deploys must supply
every variable via env files (see docs/10 §8.1 api.env / worker.env).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

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
    database_url: str = Field(default="")
    worker_database_url: str = Field(default="")
    # Worker correctness depends on connection-scoped SET ROLE and advisory
    # locks. Custom proxies must declare their actual pooling semantics; the
    # transaction value is accepted by parsing only so startup can explain why
    # that topology is unsupported.
    worker_database_pool_mode: Literal["direct", "session", "transaction"] | None = Field(
        default=None
    )
    db_pool_min_size: int = Field(default=2)
    db_pool_max_size: int = Field(default=10)
    # Transaction-pooler (PgBouncer, Supabase Transaction Pooler 6543) deployments
    # must set statement_cache_size=0 in the DSN query string (asyncpg reads
    # it from there). None = let the DSN decide (direct connections keep the
    # asyncpg default of 100).
    db_statement_cache_size: int | None = Field(default=None)

    # --- Auth (Supabase-compatible JWT) --------------------------------------
    jwt_secret: str = Field(default="")
    jwt_algorithm: str = Field(default="HS256")
    access_token_ttl_seconds: int = Field(default=3600)
    refresh_token_hmac_key: str = Field(default="")
    password_reset_hmac_key: str = Field(default="")
    refresh_token_ttl_days: int = Field(default=30, ge=1, le=90)
    refresh_cookie_name: str = Field(default="wealthsail_refresh")
    # Deprecated compatibility flag. Any true value is rejected at startup;
    # password verification never bypasses bcrypt in any environment.
    auth_dev_mode: bool = Field(default=False)
    password_reset_token_ttl_minutes: int = Field(default=30, ge=5, le=1440)

    # --- Email -------------------------------------------------------------
    web_base_url: str = Field(default="http://localhost:5173")
    smtp_host: str | None = Field(default=None)
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_starttls: bool = Field(default=True)
    smtp_username: str | None = Field(default=None)
    smtp_password: str | None = Field(default=None)
    smtp_from: str | None = Field(default=None)

    # --- DeepSeek model gateway ----------------------------------------------
    model_base_url: str = Field(default="https://api.deepseek.com/v1")
    model_api_key: str | None = Field(default=None)
    model_name: str = Field(default="deepseek-chat")
    model_timeout_seconds: float = Field(default=45.0)

    # --- Market instrument master -------------------------------------------
    # AkShare downloads whole-market symbol tables on a cold cache. Production
    # cloud egress can take slightly more than 15 seconds, so keep this timeout
    # independently configurable instead of coupling it to model/API timeouts.
    market_master_timeout_seconds: float = Field(default=30.0, ge=5.0, le=120.0)

    # --- WeCom callback / binding --------------------------------------------
    wecom_callback_token: str = Field(default="")
    wecom_callback_aes_key: str = Field(default="")
    wecom_corp_id: str = Field(default="")
    wecom_corp_secret: str = Field(default="")
    wecom_agent_id: str = Field(default="1000002")
    wecom_credential_version: str = Field(default="v1")
    # 智能机器人（API 模式·长连接）是默认关闭的隔离试验通道；MVP 的
    # send_message 只使用自建应用，禁止在结果未知时自动跨通道回退。
    wecom_bot_id: str = Field(default="")
    wecom_bot_secret: str = Field(default="")
    wecom_bot_enabled: bool = Field(default=False)
    # Salt/key for HMAC-SHA256 of one-time binding tokens (never store plaintext).
    binding_hmac_key: str = Field(default="")
    binding_token_ttl_seconds: int = Field(default=900)
    binding_token_max_attempts: int = Field(default=5)
    # Envelope-encryption key for reversible routing values (AES-256-GCM).
    routing_encryption_key: str = Field(default="")

    # --- Jobs / Outbox --------------------------------------------------------
    job_lease_timeout_seconds: int = Field(default=900)
    outbox_max_attempts: int = Field(default=5)
    outbox_base_backoff_seconds: int = Field(default=2)
    outbox_lease_seconds: int = Field(default=60)
    outbox_poll_interval_seconds: float = Field(default=15.0)
    outbox_delivery_concurrency: int = Field(default=4, ge=1, le=20)
    scheduler_tick_seconds: float = Field(default=20.0)
    job_recovery_window_minutes: int = Field(default=30, ge=5, le=240)
    job_recovery_max_attempts: int = Field(default=5, ge=1, le=20)

    # --- Independent runtime monitor ---------------------------------------
    # The monitor is a separate systemd timer, so it can report a stopped
    # Worker instead of depending on the Worker to report its own failure.
    monitor_worker_service: str = Field(default="wealthsail-worker.service")
    monitor_failed_job_window_minutes: int = Field(default=15, ge=1, le=1440)
    monitor_dead_outbox_window_hours: int = Field(default=24, ge=1, le=720)
    monitor_alert_cooldown_minutes: int = Field(default=60, ge=1, le=1440)

    # --- Recoverable Tiantian Fund import -----------------------------------
    # Mutations are guarded independently from reads so an operator can stop
    # new imports without hiding an existing DRAFT or its audit history.
    tiantian_import_enabled: bool = Field(default=False)
    tiantian_import_max_upload_bytes: int = Field(
        default=20 * 1024 * 1024, ge=1024, le=100 * 1024 * 1024
    )
    tiantian_import_max_expanded_bytes: int = Field(
        default=100 * 1024 * 1024, ge=1024, le=500 * 1024 * 1024
    )
    tiantian_import_max_entries: int = Field(default=20, ge=5, le=100)
    tiantian_import_max_compression_ratio: int = Field(default=100, ge=1, le=1000)
    tiantian_import_max_positions: int = Field(default=1000, ge=1, le=10000)
    tiantian_import_max_transactions: int = Field(default=20000, ge=1, le=100000)
    tiantian_import_max_json_depth: int = Field(default=32, ge=4, le=100)
    tiantian_import_max_text_length: int = Field(default=2000, ge=32, le=20000)
    data_task_poll_interval_seconds: float = Field(default=5.0, gt=0, le=300)
    data_task_concurrency: int = Field(default=2, ge=1, le=8)
    data_task_lease_seconds: int = Field(default=300, ge=30, le=3600)
    data_task_max_attempts: int = Field(default=5, ge=1, le=20)
    data_task_base_backoff_seconds: int = Field(default=10, ge=1, le=3600)
    fund_nav_history_page_size: int = Field(default=100, ge=10, le=500)
    fund_nav_history_max_pages: int = Field(default=50, ge=1, le=200)

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


_DEVELOPMENT_SECRET_MARKERS = ("dev-", "dev_", "change-me", "demo")


def _is_development_secret(value: str | None) -> bool:
    normalized = (value or "").strip().lower()
    return not normalized or any(marker in normalized for marker in _DEVELOPMENT_SECRET_MARKERS)


def _require_secret(name: str, value: str | None, *, min_length: int = 32) -> None:
    if _is_development_secret(value) or len((value or "").strip()) < min_length:
        raise ValueError(f"{name} must be a non-development secret of at least {min_length} characters")


def _require_distinct_secrets(secrets: dict[str, str | None]) -> None:
    normalized = {
        name: (value or "").strip()
        for name, value in secrets.items()
        if (value or "").strip()
    }
    by_value: dict[str, list[str]] = {}
    for name, value in normalized.items():
        by_value.setdefault(value, []).append(name)
    reused = [names for names in by_value.values() if len(names) > 1]
    if reused:
        names = ", ".join(sorted(reused[0]))
        raise ValueError(f"security secrets must be independent: {names}")


def _uses_supabase_transaction_pooler(dsn: str) -> bool:
    """Recognize Supabase's transaction-pooled endpoint without logging secrets."""
    try:
        parsed = urlsplit(dsn)
        hostname = (parsed.hostname or "").lower()
        return parsed.port == 6543 and hostname.endswith(".pooler.supabase.com")
    except ValueError:
        return False


def validate_runtime_settings(settings: Settings, *, process: str) -> Settings:
    """Fail closed when a production process receives the wrong authority.

    Compose/secret stores remain responsible for injecting values.  This
    function enforces the process boundary at runtime so a shared or stale env
    file cannot silently give API and Worker each other's credentials.
    """
    if process not in {"api", "worker"}:
        raise ValueError(f"unknown process type: {process}")
    if settings.auth_dev_mode:
        raise ValueError("AUTH_DEV_MODE is no longer supported; use bcrypt credentials")
    # Worker initialization installs session-scoped runtime authority, and its
    # cold-start jobs use session advisory locks. Supabase's transaction pooler
    # may move consecutive statements to different PostgreSQL backends, which
    # can invalidate SET ROLE and leak a lock. Reject that topology in every
    # environment instead of limiting the guard to production deployments.
    worker_dsn = settings.worker_database_url.strip()
    if process == "worker" and worker_dsn:
        if _uses_supabase_transaction_pooler(worker_dsn):
            raise ValueError(
                "WORKER_DATABASE_URL must use a direct or session-pooled endpoint "
                "(Supabase port 5432); transaction pooler port 6543 is not supported"
            )
        if settings.worker_database_pool_mode is None:
            raise ValueError(
                "WORKER_DATABASE_POOL_MODE is required when WORKER_DATABASE_URL is set; "
                "declare direct or session after verifying the endpoint topology"
            )
        if settings.worker_database_pool_mode == "transaction":
            raise ValueError(
                "Worker does not support transaction pooling; set "
                "WORKER_DATABASE_POOL_MODE to direct or session and use a matching endpoint"
            )
    if not settings.is_production:
        return settings

    if process == "api":
        _require_secret("JWT_SECRET", settings.jwt_secret)
        _require_secret("REFRESH_TOKEN_HMAC_KEY", settings.refresh_token_hmac_key)
        _require_secret("PASSWORD_RESET_HMAC_KEY", settings.password_reset_hmac_key)
        if not settings.database_url.strip():
            raise ValueError("DATABASE_URL is required for the API")
        _require_secret("BINDING_HMAC_KEY", settings.binding_hmac_key)
        _require_secret("ROUTING_ENCRYPTION_KEY", settings.routing_encryption_key)
        _require_secret("WECOM_CALLBACK_TOKEN", settings.wecom_callback_token, min_length=16)
        _require_distinct_secrets(
            {
                "JWT_SECRET": settings.jwt_secret,
                "REFRESH_TOKEN_HMAC_KEY": settings.refresh_token_hmac_key,
                "PASSWORD_RESET_HMAC_KEY": settings.password_reset_hmac_key,
                "BINDING_HMAC_KEY": settings.binding_hmac_key,
                "ROUTING_ENCRYPTION_KEY": settings.routing_encryption_key,
                "WECOM_CALLBACK_TOKEN": settings.wecom_callback_token,
            }
        )
        if settings.jwt_algorithm != "HS256":
            raise ValueError("JWT_ALGORITHM must be HS256")
        if len(settings.wecom_callback_aes_key.strip()) != 43:
            raise ValueError("WECOM_CALLBACK_AES_KEY must be exactly 43 characters")
        if not settings.wecom_corp_id.strip():
            raise ValueError("WECOM_CORP_ID is required for the API")
        if not settings.smtp_host or not settings.smtp_from:
            raise ValueError("SMTP_HOST and SMTP_FROM are required for password reset delivery")
        if not settings.smtp_starttls:
            raise ValueError("SMTP_STARTTLS must be true in production")
        if settings.smtp_username and not settings.smtp_password:
            raise ValueError("SMTP_PASSWORD is required when SMTP_USERNAME is configured")
        if settings.worker_database_url.strip():
            raise ValueError("WORKER_DATABASE_URL must not be provided to the API")
        if settings.wecom_corp_secret or settings.wecom_bot_secret or settings.model_api_key:
            raise ValueError("Worker/model sending credentials must not be provided to the API")
        if not settings.web_base_url.lower().startswith("https://"):
            raise ValueError("WEB_BASE_URL must use HTTPS in production")
        if any(origin == "*" for origin in settings.cors_origins):
            raise ValueError("CORS_ORIGINS must not contain '*' in production")
    else:
        if settings.jwt_secret.strip():
            raise ValueError("JWT_SECRET must not be provided to the Worker")
        if settings.refresh_token_hmac_key.strip() or settings.password_reset_hmac_key.strip():
            raise ValueError("session and password-reset secrets must not be provided to the Worker")
        if settings.binding_hmac_key.strip():
            raise ValueError("BINDING_HMAC_KEY must not be provided to the Worker")
        if settings.database_url.strip():
            raise ValueError("DATABASE_URL must not be provided to the Worker")
        if not settings.worker_database_url.strip():
            raise ValueError("WORKER_DATABASE_URL is required for the Worker")
        _require_secret("ROUTING_ENCRYPTION_KEY", settings.routing_encryption_key)
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
