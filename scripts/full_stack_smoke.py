"""Run WealthSail's production-build UI and API against a guarded remote database.

The runner intentionally starts only the API and the frontend preview server.
It never starts the Worker and gives the frontend process no database secret.

Required environment variables:
  WS_SMOKE_DATABASE_URL           wealthsail_api_login DSN (TLS required)
  WS_SMOKE_DATABASE_EXPECTED_HOST exact hostname confirmation (no scheme/port)
  WS_SMOKE_EMAIL                  dedicated smoke-test account
  WS_SMOKE_PASSWORD               dedicated smoke-test account password
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import ipaddress
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, TextIO
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
# Direct `python scripts/full_stack_smoke.py` execution otherwise places only
# `scripts/` on sys.path, while the runtime preflight imports `backend.*`.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
API_HOST = "127.0.0.1"
API_PORT = 8000
FRONTEND_HOST = "127.0.0.1"
FRONTEND_PORT = 5173

_PASSTHROUGH_ENV = {
    "APPDATA",
    "CI",
    "COMSPEC",
    "COREPACK_HOME",
    "GITHUB_ACTIONS",
    "HOME",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "LANG",
    "LC_ALL",
    "LOCALAPPDATA",
    "NODE_OPTIONS",
    "NO_PROXY",
    "PATH",
    "PATHEXT",
    "PLAYWRIGHT_BROWSERS_PATH",
    "PNPM_HOME",
    "SSL_CERT_DIR",
    "SSL_CERT_FILE",
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "TEMP",
    "TMP",
    "TMPDIR",
    "USERPROFILE",
    "WINDIR",
}


@dataclass(frozen=True)
class DatabaseTarget:
    host: str
    port: int
    database: str
    login_role: str

    @property
    def safe_label(self) -> str:
        """A credential-free label safe for CI logs."""
        return f"{self.host}:{self.port}/{self.database} as {self.login_role}"


def _is_loopback(host: str) -> bool:
    if host.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def validate_database_target(
    dsn: str, *, expected_host: str, allow_loopback: bool = False
) -> DatabaseTarget:
    """Fail closed before a CI job can connect to the wrong or privileged DB."""
    if not dsn.strip():
        raise ValueError("WS_SMOKE_DATABASE_URL is required")
    try:
        parsed = urlsplit(dsn)
        port = parsed.port or 5432
    except ValueError as exc:
        raise ValueError("WS_SMOKE_DATABASE_URL is invalid") from exc
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise ValueError("WS_SMOKE_DATABASE_URL must use postgresql://")
    if not parsed.hostname or not parsed.username or not parsed.password:
        raise ValueError("WS_SMOKE_DATABASE_URL must include host, login and password")
    loopback = _is_loopback(parsed.hostname)
    if loopback and not allow_loopback:
        raise ValueError("full-stack remote smoke must not target a loopback database")

    confirmed_host = expected_host.strip().casefold()
    if not confirmed_host:
        raise ValueError("WS_SMOKE_DATABASE_EXPECTED_HOST (expected host) is required")
    if parsed.hostname.casefold() != confirmed_host:
        raise ValueError("database host confirmation mismatch")

    # Supabase pooler usernames are ROLE.PROJECT_REF; direct connections use ROLE.
    login_role = parsed.username.split(".", 1)[0]
    if login_role != "wealthsail_api_login":
        raise ValueError("remote smoke requires the least-privileged wealthsail_api_login")

    query = parse_qs(parsed.query, keep_blank_values=True)
    if not loopback and query.get("sslmode", [""])[-1].casefold() not in {
            "require",
            "verify-ca",
            "verify-full",
        }:
        raise ValueError("remote smoke DATABASE_URL must set sslmode=require or stronger")
    if port == 6543 and query.get("statement_cache_size", [""])[-1] != "0":
        raise ValueError("port 6543 requires statement_cache_size=0")

    database = parsed.path.strip("/")
    if not database:
        raise ValueError("WS_SMOKE_DATABASE_URL must include a database name")
    return DatabaseTarget(parsed.hostname, port, database, login_role)


def _minimal_environment(source: Mapping[str, str]) -> dict[str, str]:
    return {key: value for key, value in source.items() if key.upper() in _PASSTHROUGH_ENV}


def _derived_secret(seed: str, purpose: str) -> str:
    return hashlib.sha256(f"{purpose}:{seed}".encode()).hexdigest()


def build_api_environment(
    source: Mapping[str, str], *, database_url: str, ephemeral_secret: str
) -> dict[str, str]:
    """Build a least-authority API environment; Worker/sender secrets cannot leak."""
    env = _minimal_environment(source)
    env.update(
        {
            # Staging keeps localhost cookies usable. Database authority is checked
            # independently by verify_database_authority() before startup.
            "APP_ENV": "staging",
            "DATABASE_URL": database_url,
            "DB_POOL_MIN_SIZE": "1",
            "DB_POOL_MAX_SIZE": "2",
            "DB_STATEMENT_CACHE_SIZE": "0",
            "JWT_SECRET": ephemeral_secret,
            "REFRESH_TOKEN_HMAC_KEY": _derived_secret(ephemeral_secret, "refresh"),
            "PASSWORD_RESET_HMAC_KEY": _derived_secret(ephemeral_secret, "reset"),
            "BINDING_HMAC_KEY": _derived_secret(ephemeral_secret, "binding"),
            "ROUTING_ENCRYPTION_KEY": base64.urlsafe_b64encode(
                hashlib.sha256(f"routing:{ephemeral_secret}".encode()).digest()
            ).decode(),
            "AUTH_DEV_MODE": "false",
            "CORS_ORIGINS": '["http://127.0.0.1:5173"]',
            "WEB_BASE_URL": "http://127.0.0.1:5173",
            "TIANTIAN_IMPORT_ENABLED": "false",
            "LOG_LEVEL": "INFO",
        }
    )
    return env


async def verify_database_authority(database_url: str) -> None:
    """Use the application's own boundary check before serving any HTTP request."""
    from backend.common.database import create_pool, verify_runtime_login_boundary

    pool = await create_pool(database_url, min_size=1, max_size=1)
    try:
        await verify_runtime_login_boundary(pool, process="api", skip=False)
    finally:
        await pool.close()


def _command(name: str) -> str:
    executable = shutil.which(name)
    if not executable:
        raise RuntimeError(f"required executable is not on PATH: {name}")
    return executable


def _assert_port_available(host: str, port: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
        except OSError as exc:
            raise RuntimeError(f"required local port is already in use: {host}:{port}") from exc


def _popen(command: list[str], *, cwd: Path, env: Mapping[str, str], log: TextIO):
    kwargs: dict[str, object] = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(  # noqa: S603 - command is an explicit executable/list
        command,
        cwd=cwd,
        env=dict(env),
        stdout=log,
        stderr=subprocess.STDOUT,
        text=True,
        **kwargs,
    )


def _stop_process(process: subprocess.Popen[str] | None) -> None:
    if process is None or process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(  # noqa: S603,S607 - exact child PID, no shell expansion
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=10)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()


def _wait_for_http(
    url: str, *, process: subprocess.Popen[str], label: str, timeout_seconds: int = 90
) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error = "not started"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"{label} exited early with code {process.returncode}")
        try:
            with urllib.request.urlopen(url, timeout=3) as response:  # noqa: S310
                if response.status == 200:
                    print(f"OK  {label}: {url}")
                    return
                last_error = f"HTTP {response.status}"
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last_error = str(exc)
        time.sleep(0.5)
    raise RuntimeError(f"{label} did not become ready: {last_error}")


def _run_checked(
    command: list[str], *, cwd: Path, env: Mapping[str, str], log_path: Path
) -> None:
    with log_path.open("w", encoding="utf-8") as log:
        completed = subprocess.run(  # noqa: S603 - explicit executable/list
            command,
            cwd=cwd,
            env=dict(env),
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
    if completed.returncode != 0:
        raise RuntimeError(f"command failed ({completed.returncode}); see {log_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--artifacts-dir",
        type=Path,
        default=ROOT / "artifacts" / "full-stack-smoke",
    )
    parser.add_argument(
        "--skip-frontend-build",
        action="store_true",
        help="Use an existing frontend/dist (intended only for local iteration)",
    )
    parser.add_argument(
        "--allow-loopback-database",
        action="store_true",
        help="Allow the ephemeral PostgreSQL service used by pull-request CI",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    database_url = os.environ.get("WS_SMOKE_DATABASE_URL", "").strip()
    expected_host = os.environ.get("WS_SMOKE_DATABASE_EXPECTED_HOST", "").strip()
    email = os.environ.get("WS_SMOKE_EMAIL", "").strip()
    password = os.environ.get("WS_SMOKE_PASSWORD", "")
    if not email or not password:
        raise SystemExit("WS_SMOKE_EMAIL and WS_SMOKE_PASSWORD are required")

    target = validate_database_target(
        database_url,
        expected_host=expected_host,
        allow_loopback=args.allow_loopback_database,
    )
    print(f"TARGET {target.safe_label}")
    print("MODE   local API + production frontend build; Worker disabled; business writes blocked")

    artifacts = args.artifacts_dir.resolve()
    artifacts.mkdir(parents=True, exist_ok=True)
    _assert_port_available(API_HOST, API_PORT)
    _assert_port_available(FRONTEND_HOST, FRONTEND_PORT)

    print("CHECK  database login boundary")
    asyncio.run(verify_database_authority(database_url))

    pnpm = _command("pnpm")
    node = _command("node")
    frontend_env = _minimal_environment(os.environ)
    if not args.skip_frontend_build:
        print("BUILD  frontend production bundle")
        _run_checked(
            [pnpm, "run", "build"],
            cwd=FRONTEND,
            env=frontend_env,
            log_path=artifacts / "frontend-build.log",
        )
    elif not (FRONTEND / "dist" / "index.html").exists():
        raise RuntimeError("--skip-frontend-build requires frontend/dist/index.html")

    ephemeral_secret = secrets.token_urlsafe(48)
    api_env = build_api_environment(
        os.environ,
        database_url=database_url,
        ephemeral_secret=ephemeral_secret,
    )
    ui_env = _minimal_environment(os.environ)
    ui_env.update(
        {
            "WS_SMOKE_BASE_URL": f"http://{FRONTEND_HOST}:{FRONTEND_PORT}",
            "WS_SMOKE_EMAIL": email,
            "WS_SMOKE_PASSWORD": password,
            "WS_SMOKE_ARTIFACT_DIR": str(artifacts),
        }
    )

    api_process: subprocess.Popen[str] | None = None
    frontend_process: subprocess.Popen[str] | None = None
    try:
        with (artifacts / "backend.log").open("w", encoding="utf-8") as api_log, (
            artifacts / "frontend-preview.log"
        ).open("w", encoding="utf-8") as frontend_log:
            print("START  API (staging cookie mode, remote least-privileged DB)")
            api_process = _popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "backend.main:app",
                    "--host",
                    API_HOST,
                    "--port",
                    str(API_PORT),
                ],
                cwd=ROOT,
                env=api_env,
                log=api_log,
            )
            _wait_for_http(
                f"http://{API_HOST}:{API_PORT}/healthz",
                process=api_process,
                label="API + database",
            )

            print("START  frontend preview")
            frontend_process = _popen(
                [
                    pnpm,
                    "exec",
                    "vite",
                    "preview",
                    "--host",
                    FRONTEND_HOST,
                    "--port",
                    str(FRONTEND_PORT),
                    "--strictPort",
                ],
                cwd=FRONTEND,
                env=frontend_env,
                log=frontend_log,
            )
            _wait_for_http(
                f"http://{FRONTEND_HOST}:{FRONTEND_PORT}/login",
                process=frontend_process,
                label="frontend production preview",
            )

            print("TEST   login -> dashboard -> account -> refresh -> logout")
            _run_checked(
                [node, "scripts/ui-smoke.cjs"],
                cwd=FRONTEND,
                env=ui_env,
                log_path=artifacts / "ui-smoke.log",
            )
        print(f"PASS   full-stack smoke; artifacts: {artifacts}")
        return 0
    finally:
        _stop_process(frontend_process)
        _stop_process(api_process)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError) as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
