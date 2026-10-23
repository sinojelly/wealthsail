"""Generate a fresh NAS deployment configuration without storing DB admin secrets.

Uses only the standard library so it also runs in an unmodified Python image.
Existing files are never overwritten: retain these secrets across upgrades.
"""

from __future__ import annotations

import argparse
import base64
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
from urllib.parse import quote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
RESERVED_DATABASES = frozenset({"postgres", "template0", "template1", "prismscore_db"})


def validate_database_name(database: str) -> str:
    if (not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", database)
            or database in RESERVED_DATABASES):
        raise ValueError("database must be a dedicated lowercase application database name")
    return database


def validate_web_origin(value: str) -> str:
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.path not in ("", "/")
            or parsed.query or parsed.fragment):
        raise ValueError("WEB_BASE_URL must be an HTTPS origin without a path, credentials or query")
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError("WEB_BASE_URL must be a valid HTTPS origin") from exc
    if any(char.isspace() for char in value):
        raise ValueError("WEB_BASE_URL must be a valid HTTPS origin")
    try:
        ipaddress.ip_address(parsed.hostname)
    except ValueError:
        try:
            hostname = parsed.hostname.encode("idna").decode("ascii").rstrip(".")
        except UnicodeError as exc:
            raise ValueError("WEB_BASE_URL must have a valid HTTPS hostname") from exc
        if len(hostname) > 253 or not all(
            re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
            for label in hostname.split(".")
        ):
            raise ValueError("WEB_BASE_URL must have a valid HTTPS hostname")
    return value.rstrip("/")


def database_dsn(*, host: str, port: int, database: str, user: str,
                 password: str, sslmode: str = "disable") -> str:
    authority = f"[{host}]" if ":" in host else host
    return (f"postgresql://{quote(user, safe='')}:{quote(password, safe='')}@"
            f"{authority}:{port}/{database}?sslmode={sslmode}")


def _env_text(values: dict[str, str]) -> str:
    # Single-quoted dotenv values suppress Compose ${...} interpolation. JSON
    # content (CORS) and URL-encoded DSNs contain no literal single quotes.
    return "# Generated once; keep private and retain across upgrades.\n" + "".join(
        f"{key}='{value}'\n" for key, value in values.items()
    )


def generate_configuration(
    root: Path, *, web_base_url: str, host: str = "postgres", port: int = 5432,
    database: str = "wealthsail", network: str = "global-postgres-network",
    admin_user: str = "admin", web_bind_address: str = "127.0.0.1",
    web_port: int = 8080, sslmode: str = "disable",
) -> dict[str, Path]:
    origin = validate_web_origin(web_base_url)
    validate_database_name(database)
    for label, value in (("host", host), ("network", network), ("admin user", admin_user),
                         ("web bind address", web_bind_address)):
        if not value or not re.fullmatch(r"[A-Za-z0-9_.:\-]+", value):
            raise ValueError(f"invalid {label}")
    if not 1 <= port <= 65535 or not 1 <= web_port <= 65535:
        raise ValueError("ports must be between 1 and 65535")
    if sslmode not in {"disable", "require", "verify-full"}:
        raise ValueError("sslmode must be disable, require or verify-full")
    paths = {
        "api": root / "deployment/env/api.env",
        "worker": root / "deployment/env/worker.env",
        "nas": root / "deployment/nas.env",
    }
    for path in paths.values():
        if path.exists():
            raise FileExistsError(f"configuration exists; preserve its secrets: {path}")
    routing_key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")
    api = {
        "APP_ENV": "production",
        "DATABASE_URL": database_dsn(host=host, port=port, database=database,
                                     user="wealthsail_api_login", password=secrets.token_hex(32),
                                     sslmode=sslmode),
        "JWT_SECRET": secrets.token_hex(32),
        "REFRESH_TOKEN_HMAC_KEY": secrets.token_hex(32),
        "PASSWORD_RESET_HMAC_KEY": secrets.token_hex(32),
        "BINDING_HMAC_KEY": secrets.token_hex(32),
        "ROUTING_ENCRYPTION_KEY": routing_key,
        "WECOM_ENABLED": "false",
        "PASSWORD_RESET_EMAIL_ENABLED": "false",
        "CORS_ORIGINS": json.dumps([origin]),
        "WEB_BASE_URL": origin,
        "DB_POOL_MIN_SIZE": "1",
        "DB_POOL_MAX_SIZE": "4",
        "TIANTIAN_IMPORT_ENABLED": "true",
        "AUTH_DEV_MODE": "false",
    }
    worker = {
        "APP_ENV": "production",
        "WORKER_DATABASE_URL": database_dsn(host=host, port=port, database=database,
                                            user="wealthsail_worker_login", password=secrets.token_hex(32),
                                            sslmode=sslmode),
        "WORKER_DATABASE_POOL_MODE": "direct",
        "ROUTING_ENCRYPTION_KEY": routing_key,
        "WECOM_ENABLED": "false",
        "DB_POOL_MIN_SIZE": "1",
        "DB_POOL_MAX_SIZE": "6",
        "DATA_TASK_CONCURRENCY": "2",
        "OUTBOX_DELIVERY_CONCURRENCY": "2",
        "DAILY_RESEARCH_ENABLED": "false",
        "AUTH_DEV_MODE": "false",
    }
    nas = {
        "API_ENV_FILE": "deployment/env/api.env",
        "WORKER_ENV_FILE": "deployment/env/worker.env",
        "POSTGRES_NETWORK_NAME": network,
        "POSTGRES_ADMIN_USER": admin_user,
        "WEB_BIND_ADDRESS": web_bind_address,
        "WEB_PORT": str(web_port),
    }
    created: list[Path] = []
    try:
        for name, values in (("api", api), ("worker", worker), ("nas", nas)):
            path = paths[name]
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            created.append(path)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(_env_text(values))
    except Exception:
        for path in created:
            path.unlink(missing_ok=True)
        raise
    return paths


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--web-base-url", required=True, help="public HTTPS origin")
    parser.add_argument("--host", default="postgres")
    parser.add_argument("--port", type=int, default=5432)
    parser.add_argument("--database", default="wealthsail")
    parser.add_argument("--network", default="global-postgres-network")
    parser.add_argument("--admin-user", default="admin")
    parser.add_argument("--web-bind-address", default="127.0.0.1")
    parser.add_argument("--web-port", type=int, default=8080)
    parser.add_argument("--sslmode", choices=("disable", "require", "verify-full"), default="disable")
    args = vars(parser.parse_args())
    try:
        paths = generate_configuration(ROOT, **args)
    except (ValueError, FileExistsError) as exc:
        parser.exit(2, f"{exc}\n")
    for name, path in paths.items():
        print(f"{name}: {path.relative_to(ROOT)}")
    print("Configuration generated. Keep private; the database admin password was not stored.")


if __name__ == "__main__":
    main()
