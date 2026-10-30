"""Build and run the real Windows stack against portable PostgreSQL.

Invoked by start-local.ps1. Standard-library bootstrap; runtime imports are lazy.
All children run with a filtered environment and project-local temp/cache paths.
"""

from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
import ipaddress
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / ".local"
VENV_PY = ROOT / ".venv-local/Scripts/python.exe"
EMAIL = "local-owner@wealthsail.test"
PASSWORD = "LocalDemo-2026!"


def validate_local_target(dsn: str) -> None:
    """Seeding is restricted to the dedicated local development database."""
    parts = urlsplit(dsn)
    try:
        loopback = ipaddress.ip_address(parts.hostname or "").is_loopback
    except ValueError:
        loopback = parts.hostname == "localhost"
    if not (parts.scheme == "postgresql" and loopback
            and parts.username == "postgres" and parts.path == "/wealthsail_local"):
        raise ValueError("Local seed requires postgres on loopback /wealthsail_local")


def child_environment(source: dict[str, str], directory: Path) -> dict[str, str]:
    allowed = {
        "path", "systemroot", "windir", "comspec", "pathext", "userprofile",
        "appdata", "localappdata", "programfiles", "programfiles(x86)",
        "programdata", "homedrive", "homepath", "number_of_processors",
        "processor_architecture", "codex_home", "home", "codex_ca_certificate",
        "http_proxy", "https_proxy", "no_proxy", "ssl_cert_file", "ssl_cert_dir",
    }
    env = {k: v for k, v in source.items() if k.lower() in allowed}
    env.update({"TEMP": str(directory / "tmp"), "TMP": str(directory / "tmp"),
                "PIP_CACHE_DIR": str(directory / "cache/pip"),
                "npm_config_cache": str(directory / "cache/npm"),
                "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1"})
    return env


def node_supported(version: str) -> bool:
    parts = tuple(int(part) for part in version.strip().lstrip("v").split("."))
    return parts >= (22, 12, 0)


def run(command: list[str], env: dict[str, str], *, cwd: Path = ROOT,
        capture: bool = False) -> str:
    result = subprocess.run(command, cwd=cwd, env=env, check=True,
                            text=True, encoding="utf-8", errors="replace",
                            stdout=subprocess.PIPE if capture else None,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    return result.stdout.strip() if capture else ""


def require_tools(env: dict[str, str]) -> None:
    for executable in ("node", "pnpm.cmd"):
        if not shutil.which(executable):
            raise RuntimeError(f"Missing {executable}. See docs/26.Windows-local-development.md")
    if not node_supported(run(["node", "--version"], env, capture=True)):
        raise RuntimeError("Node.js 22.12+ required (recommend Node.js 22 or 24)")


def check_ports(ports: list[int]) -> None:
    if len(set(ports)) != len(ports):
        raise RuntimeError("Database, API and Web ports must be different")
    # pg_ctl checks the database port and only reuses our own .local/pgdata.
    for port in ports[1:]:
        with socket.socket() as sock:
            try:
                sock.bind(("127.0.0.1", port))
            except OSError as exc:
                raise RuntimeError(f"Port {port} occupied; port alone does not prove managed ownership. "
                                   "Use -Action Status/-Action Stop for recorded services, or change -ApiPort/-WebPort") from exc


def configure(args: argparse.Namespace) -> dict[str, str]:
    settings_path = LOCAL / "secrets.json"
    if settings_path.exists():
        settings = json.loads(settings_path.read_text("utf-8"))
    else:
        settings = {key: secrets.token_hex(32) for key in (
            "postgres_password", "api_password", "jwt", "refresh", "reset", "binding")}
        settings["routing"] = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
        settings_path.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    configure_macro(settings, args.db_port)
    url = f"http://127.0.0.1:{args.web_port}"
    api_settings = {
        "APP_ENV": "development", "AUTH_DEV_MODE": "false",
        "DATABASE_URL": f"postgresql://wealthsail_api_login:{settings['api_password']}"
                        f"@127.0.0.1:{args.db_port}/wealthsail_local?sslmode=disable",
        "JWT_SECRET": settings["jwt"], "REFRESH_TOKEN_HMAC_KEY": settings["refresh"],
        "PASSWORD_RESET_HMAC_KEY": settings["reset"], "BINDING_HMAC_KEY": settings["binding"],
        "ROUTING_ENCRYPTION_KEY": settings["routing"],
        "DB_POOL_MIN_SIZE": "1", "DB_POOL_MAX_SIZE": "4",
        "WEB_BASE_URL": url, "CORS_ORIGINS": json.dumps([url]),
        "TIANTIAN_IMPORT_ENABLED": "true",
        "DAILY_RESEARCH_ENABLED": os.environ.get("DAILY_RESEARCH_ENABLED", "false"),
        "DAILY_RESEARCH_START_WINDOW_MINUTES": os.environ.get("DAILY_RESEARCH_START_WINDOW_MINUTES", "120"),
        "PRE_MARKET_REPORTS_ENABLED": os.environ.get("PRE_MARKET_REPORTS_ENABLED", "false"),
        "MORNING_OPPORTUNITY_PUSH_ENABLED": os.environ.get("MORNING_OPPORTUNITY_PUSH_ENABLED", "false"),
        "FUND_1430_ENABLED": os.environ.get("FUND_1430_ENABLED", "false"),
        "POST_MARKET_REVIEW_ENABLED": os.environ.get("POST_MARKET_REVIEW_ENABLED", "false"),
        "INTRADAY_TRACKING_ENABLED": os.environ.get("INTRADAY_TRACKING_ENABLED", "false"),
        "DAILY_RESEARCH_DEBUG_ENABLED": os.environ.get("DAILY_RESEARCH_DEBUG_ENABLED", "true"),
        **local_codex_settings(),
    }
    (LOCAL / "api.env").write_text(
        "\n".join(f"{key}={value}" for key, value in api_settings.items()) + "\n",
        encoding="utf-8")
    return settings


def local_codex_settings() -> dict[str, str]:
    """Explicit local launcher opt-in; never inject API keys into model calls."""
    from backend.integrations.codex_research import resolve_codex_executable

    executable = resolve_codex_executable(
        os.environ.get("CODEX_PATH") or os.environ.get("DAILY_RESEARCH_CODEX_PATH")
    )
    values = {"CODEX_PATH": json.dumps(Path(executable).as_posix() if executable else "")}
    if os.environ.get("CODEX_MODEL"):
        values["CODEX_MODEL"] = json.dumps(os.environ["CODEX_MODEL"])
    return values


def configure_macro(settings: dict[str, str], db_port: int) -> None:
    """Give the source-only collector its own restricted local login/config."""
    if "worker_password" not in settings:
        settings["worker_password"] = secrets.token_hex(32)
        (LOCAL / "secrets.json").write_text(json.dumps(settings, indent=2), encoding="utf-8")
    from backend.integrations.codex_research import resolve_codex_executable
    executable = resolve_codex_executable(os.environ.get("DAILY_RESEARCH_CODEX_PATH"))
    macro_settings = {
        "APP_ENV": "development",
        "WORKER_DATABASE_URL": f"postgresql://wealthsail_worker_login:{settings['worker_password']}"
                               f"@127.0.0.1:{db_port}/wealthsail_local?sslmode=disable",
        "WORKER_DATABASE_POOL_MODE": "direct",
        "DB_POOL_MIN_SIZE": "1", "DB_POOL_MAX_SIZE": "2",
        "DAILY_RESEARCH_ENABLED": os.environ.get("DAILY_RESEARCH_ENABLED", "false"),
        "DAILY_RESEARCH_START_WINDOW_MINUTES": os.environ.get("DAILY_RESEARCH_START_WINDOW_MINUTES", "120"),
        "PRE_MARKET_REPORTS_ENABLED": os.environ.get("PRE_MARKET_REPORTS_ENABLED", "false"),
        "MORNING_OPPORTUNITY_PUSH_ENABLED": os.environ.get("MORNING_OPPORTUNITY_PUSH_ENABLED", "false"),
        "FUND_1430_ENABLED": os.environ.get("FUND_1430_ENABLED", "false"),
        "POST_MARKET_REVIEW_ENABLED": os.environ.get("POST_MARKET_REVIEW_ENABLED", "false"),
        "INTRADAY_TRACKING_ENABLED": os.environ.get("INTRADAY_TRACKING_ENABLED", "false"),
        "DAILY_RESEARCH_CATCH_UP": "true",
        "DAILY_RESEARCH_DEBUG_ENABLED": os.environ.get("DAILY_RESEARCH_DEBUG_ENABLED", "true"),
        "DAILY_RESEARCH_CODEX_PATH": json.dumps(Path(executable).as_posix() if executable else ""),
        "DAILY_RESEARCH_AUDIT_DIR": json.dumps(os.environ.get("DAILY_RESEARCH_AUDIT_DIR", (LOCAL / "research-audit").as_posix())),
        **local_codex_settings(),
    }
    if os.environ.get("DAILY_RESEARCH_MODEL"):
        macro_settings["DAILY_RESEARCH_MODEL"] = json.dumps(os.environ["DAILY_RESEARCH_MODEL"])
    # Keep the operator's scoped Phase2 configuration across generated restarts.
    from dotenv import dotenv_values
    previous = dotenv_values(LOCAL / "macro.env")
    for name in ("ALPHA_PUBLIC_ENABLED", "ALPHA_OPPORTUNITY_ENABLED", "WECOM_OPPORTUNITY_WEBHOOK_URL", "OPPORTUNITY_TENANT_ID", "OPPORTUNITY_USER_ID", "DAILY_RESEARCH_AUDIT_DIR"):
        value = os.environ.get(name, previous.get(name))
        if value is not None:
            macro_settings[name] = json.dumps(value)
    (LOCAL / "macro.env").write_text(
        "\n".join(f"{key}={value}" for key, value in macro_settings.items()) + "\n", encoding="utf-8")


def refresh_local_macro(args: argparse.Namespace, env: dict[str, str]) -> None:
    """Upgrade and backfill macro facts in the already running local database."""
    path = LOCAL / "secrets.json"
    if not path.exists():
        raise RuntimeError("Start the local services first, then use -Action RefreshMacro")
    state = read_state()
    if not state or state.get("phase") != "ready" or not state_alive(state, env):
        raise RuntimeError("Local services must be ready; use -Action Status and avoid concurrent Start/Stop")
    settings = json.loads(path.read_text("utf-8"))
    db_port = state.get("db_port", args.db_port)
    configure_macro(settings, db_port)
    python = select_python(env, skip_install=True)
    run([str(python), str(Path(__file__).resolve()), "--initialize", "--db-port", str(db_port)], env)
    run([str(python), "-m", "backend.jobs.macro_worker", "--once"],
        {**env, "WEALTHSAIL_ENV_FILE": str(LOCAL / "macro.env")})


def refresh_local_research(args: argparse.Namespace, env: dict[str, str]) -> None:
    """Explicitly enqueue one development research version, then wait for it."""
    path = LOCAL / "secrets.json"
    if not path.exists():
        raise RuntimeError("Start local services before RefreshResearch")
    state = read_state()
    if not state or state.get("phase") != "ready" or not state_alive(state, env):
        raise RuntimeError("Local services must be ready before RefreshResearch")
    if state.get("skip_macro"):
        raise RuntimeError("RefreshResearch requires the local macro Worker; restart without -SkipMacro")
    settings = json.loads(path.read_text("utf-8"))
    db_port = state.get("db_port", args.db_port)
    configure_macro(settings, db_port)
    python = select_python(env, skip_install=True)
    run([str(python), str(Path(__file__).resolve()), "--initialize", "--db-port", str(db_port)], env)
    command = [str(python), "-m", "backend.jobs.research_debug", "--email", EMAIL]
    if getattr(args, "research_request_key", None):
        command.extend(["--request-key", str(args.research_request_key)])
    run(command, {**env, "WEALTHSAIL_ENV_FILE": str(LOCAL / "macro.env")})


def select_python(env: dict[str, str], *, skip_install: bool) -> Path:
    for candidate in (ROOT / ".venv/Scripts/python.exe", ROOT / ".venv-local/Scripts/python.exe"):
        if candidate.exists():
            version = run([str(candidate), "-c", "import sys; print('%d.%d' % sys.version_info[:2])"],
                          env, capture=True)
            if version == "3.12":
                return candidate
    if skip_install:
        raise RuntimeError("No Python 3.12 virtual environment; run without -SkipInstall first")
    candidate = ROOT / ".venv-local/Scripts/python.exe"
    if candidate.exists():
        raise RuntimeError("Existing .venv-local must use Python 3.12; recreate that environment")
    run([sys.executable, "-m", "venv", str(ROOT / ".venv-local")], env)
    return candidate


def process_info(pid: int, env: dict[str, str]) -> dict | None:
    # Read creation time to distinguish a live supervisor from a recycled PID.
    code = (f"$ErrorActionPreference='Stop'; Get-CimInstance Win32_Process -Filter 'ProcessId = {int(pid)}' -ErrorAction Stop | "
            "Select-Object ProcessId,ParentProcessId,Name,ExecutablePath,CommandLine,@{Name='created';Expression={"
            "$_.CreationDate.ToUniversalTime().ToString('o')}} | ConvertTo-Json -Compress")
    output = run(["powershell.exe", "-NoProfile", "-Command", code], env, capture=True)
    return json.loads(output) if output and output != "null" else None


def service_process_inventory(pids: list[int], env: dict[str, str]) -> list[dict]:
    ids = ",".join(str(int(pid)) for pid in pids)
    code = ("$ErrorActionPreference='Stop'; $all=@(Get-CimInstance Win32_Process -ErrorAction Stop); "
            f"$ids=@({ids}); do {{ $old=$ids.Count; $ids=@($ids + @($all | Where-Object {{$_.ParentProcessId -in $ids}} | "
            "Select-Object -ExpandProperty ProcessId) | Select-Object -Unique) } while ($ids.Count -gt $old); "
            "$all | Where-Object {$_.ProcessId -in $ids} | "
            "Select-Object ProcessId,ParentProcessId,Name,ExecutablePath,CommandLine,@{Name='created';Expression={"
            "$_.CreationDate.ToUniversalTime().ToString('o')}} | ConvertTo-Json -Compress")
    output = run(["powershell.exe", "-NoProfile", "-Command", code], env, capture=True)
    data = json.loads(output) if output and output != "null" else []
    return data if isinstance(data, list) else [data]


def terminate_service_process(pid: int, env: dict[str, str]) -> None:
    subprocess.run(["taskkill.exe", "/PID", str(pid), "/T", "/F"], env=env,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   creationflags=subprocess.CREATE_NO_WINDOW, timeout=10, check=False)


def remaining_service_records(env):
    from scripts.local_processes import remaining_services
    return remaining_services(LOCAL / "services.json", ROOT, lambda pid: process_info(pid, env))


def cleanup_service_records(env):
    from scripts.local_processes import cleanup_services
    return cleanup_services(LOCAL / "services.json", ROOT, lambda pid: process_info(pid, env),
                            lambda pid: terminate_service_process(pid, env))


def read_state() -> dict | None:
    try:
        return json.loads((LOCAL / "run.json").read_text("utf-8"))
    except FileNotFoundError:
        return None


def write_state(state: dict) -> None:
    pending = LOCAL / "run.json.tmp"
    pending.write_text(json.dumps(state), encoding="utf-8")
    pending.replace(LOCAL / "run.json")


def state_alive(state: dict, env: dict[str, str]) -> bool:
    info = process_info(state["pid"], env)
    return bool(info and info["Name"] == "python.exe" and info["created"] == state["created"])


def stop_requested() -> bool:
    state = read_state()
    flag = LOCAL / "stop-request"
    return bool(state and flag.exists() and flag.read_text("utf-8") == state["run_id"])


def check_stop_request() -> None:
    if stop_requested():
        raise KeyboardInterrupt


@contextmanager
def launcher_lock(path: Path):
    """Acquire the Windows byte lock before any access to the protected byte."""
    import msvcrt

    lock = path.open("a+b")
    try:
        lock.seek(0)
        try:
            # Windows permits locking beyond EOF; no seed byte is necessary.
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise RuntimeError("Already running; use -Action Status or -Action Stop") from None
        yield
    finally:
        lock.close()


def runtime_action(action: str, env: dict[str, str], supplied_bin: Path | None) -> None:
    from scripts.local_postgres import stop_database

    state = read_state()
    alive = bool(state and state_alive(state, env))
    if action == "status":
        if alive:
            print(f"{state['phase']}: http://127.0.0.1:{state['web_port']} (PID {state['pid']})")
        else:
            remaining = remaining_service_records(env)
            print("Supervisor not running; managed services remain. Use -Action Stop."
                  if remaining else "Not running; no live recorded API/frontend services.")
        return
    if alive:
        (LOCAL / "stop-request").write_text(state["run_id"], encoding="utf-8")
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            if not (LOCAL / "run.json").exists():
                print("Stopped API/frontend/PostgreSQL. Database data retained.")
                return
            time.sleep(0.5)
        raise RuntimeError("Stop requested; startup or shutdown still in progress. Check -Action Status/logs")
    # Recovery is serialized with Start; never infer ownership from a port.
    with launcher_lock(LOCAL / "launcher.lock"):
        state = read_state()
        if state and state_alive(state, env):
            raise RuntimeError("Launcher started concurrently; retry -Action Stop")
        manifest_exists = (LOCAL / "services.json").exists()
        try:
            remaining = cleanup_service_records(env)
        finally:
            binary_dir = supplied_bin or (Path(state["postgres_bin"]) if state else LOCAL / "postgres/pgsql/bin")
            if (binary_dir / "pg_ctl.exe").exists():
                stop_database(binary_dir, LOCAL, env)
        if remaining:
            raise RuntimeError("Some managed services could not be stopped; ownership records retained. Retry -Action Stop")
        if state:
            current = read_state()
            if current and current.get("run_id") == state.get("run_id"):
                (LOCAL / "run.json").unlink(missing_ok=True)
        print("Stopped recorded API/frontend/macro services and managed PostgreSQL; data retained."
              if manifest_exists else "Supervisor not running; no API/frontend ownership records. Unrecorded processes were not stopped.")


async def initialize_database(dsn: str, api_password: str, worker_password: str | None = None) -> None:
    validate_local_target(dsn)
    import asyncpg
    from backend.common.auth import hash_password
    from backend.common.database import create_pool, verify_runtime_login_boundary
    from scripts.migrate import load_migrations, migrate

    # A new portable cluster initially contains only postgres/template databases.
    parts = urlsplit(dsn)
    admin_dsn = urlunsplit(parts._replace(path="/postgres"))
    admin = await asyncpg.connect(admin_dsn)
    try:
        if not await admin.fetchval("SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname='wealthsail_local')"):
            await admin.execute("CREATE DATABASE wealthsail_local")
    finally:
        await admin.close()
    await migrate(dsn, load_migrations(ROOT / "migrations"), dry_run=False)
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute((ROOT / "scripts/provision_supabase_roles.sql").read_text("utf-8"))
        literal = await conn.fetchval("SELECT quote_literal($1::text)", api_password)
        await conn.execute(f"ALTER ROLE wealthsail_api_login WITH PASSWORD {literal}")
        if worker_password is not None:
            worker_literal = await conn.fetchval("SELECT quote_literal($1::text)", worker_password)
            await conn.execute(f"ALTER ROLE wealthsail_worker_login WITH PASSWORD {worker_literal}")
        async with conn.transaction():
            await conn.execute("""CREATE TABLE IF NOT EXISTS public.wealthsail_local_seed
                                  (version integer PRIMARY KEY, created_at timestamptz DEFAULT now())""")
            # Serialize repeated launches; do not reset a user's local edits.
            await conn.execute("SELECT pg_advisory_xact_lock(853271905)")
            if not await conn.fetchval("SELECT EXISTS (SELECT 1 FROM public.wealthsail_local_seed)"):
                password_hash = hash_password(PASSWORD)
                await conn.execute("""
                    INSERT INTO identity.tenants (id,name,status,base_currency)
                    VALUES ('00000000-0000-0000-0000-00000000d102','本地测试工作台','ACTIVE','CNY')
                """)
                await conn.execute("""INSERT INTO identity.profiles (id,email,nickname,password_hash)
                    VALUES ('00000000-0000-0000-0000-00000000d101',$1,'本地测试用户',$2)""",
                                   EMAIL, password_hash)
                await conn.execute("""
                    INSERT INTO identity.tenant_members (tenant_id,user_id,role,status)
                    VALUES ('00000000-0000-0000-0000-00000000d102',
                            '00000000-0000-0000-0000-00000000d101','OWNER','ACTIVE');
                    INSERT INTO identity.accounts (id,tenant_id,account_type,name,ownership_scope)
                    VALUES ('00000000-0000-0000-0000-00000000d103',
                            '00000000-0000-0000-0000-00000000d102','MANUAL','示例现金账户','PERSONAL');
                    INSERT INTO identity.account_members
                        (tenant_id,account_id,user_id,relationship,permission_role,is_primary,created_by)
                    VALUES ('00000000-0000-0000-0000-00000000d102',
                            '00000000-0000-0000-0000-00000000d103',
                            '00000000-0000-0000-0000-00000000d101',
                            'BENEFICIAL_OWNER','OWNER',true,'00000000-0000-0000-0000-00000000d101');
                    INSERT INTO ledger.ledger_events
                        (id,tenant_id,account_id,event_type,status,trade_date,idempotency_key,description)
                    VALUES ('00000000-0000-0000-0000-00000000d104',
                            '00000000-0000-0000-0000-00000000d102',
                            '00000000-0000-0000-0000-00000000d103',
                            'CASH_IN','CONFIRMED',CURRENT_DATE - 6,'LOCAL_INITIAL_CASH','本地示例资金');
                    INSERT INTO ledger.ledger_legs (tenant_id,event_id,leg_type,currency,amount)
                    VALUES ('00000000-0000-0000-0000-00000000d102',
                            '00000000-0000-0000-0000-00000000d104','CASH','CNY',100000);
                    INSERT INTO ledger.account_nav_snapshots
                        (tenant_id,account_id,as_of,cash_balance,positions_market_value,total_nav,
                         daily_pnl,cumulative_pnl,data_quality_status,known_value,
                         valuation_coverage_pct,unpriced_position_count)
                    SELECT '00000000-0000-0000-0000-00000000d102'::uuid,
                           '00000000-0000-0000-0000-00000000d103'::uuid,
                           CURRENT_DATE - day,100000,0,100000,0,0,'FRESH',100000,100,0
                    FROM generate_series(0,6) AS day;
                    INSERT INTO public.wealthsail_local_seed (version) VALUES (1);
                """)
    finally:
        await conn.close()
    parts = urlsplit(dsn)
    api_dsn = f"postgresql://wealthsail_api_login:{api_password}@{parts.hostname}:{parts.port}{parts.path}?sslmode=disable"
    pool = await create_pool(api_dsn, min_size=1, max_size=1)
    try:
        await verify_runtime_login_boundary(pool, process="api")
    finally:
        await pool.close()
    if worker_password is not None:
        worker_dsn = f"postgresql://wealthsail_worker_login:{worker_password}@{parts.hostname}:{parts.port}{parts.path}?sslmode=disable"
        worker_pool = await create_pool(worker_dsn, min_size=1, max_size=1, runtime_role="wealthsail_worker")
        try:
            await verify_runtime_login_boundary(worker_pool, process="worker")
        finally:
            await worker_pool.close()
    print("Local database ready (migrations, restricted API role, OWNER account)")


def get_json(url: str, *, data: dict | None = None, token: str | None = None) -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(url, data=json.dumps(data).encode() if data is not None else None,
                      headers=headers)
    with urlopen(request, timeout=3) as response:
        return json.load(response)


def wait_ready(url: str, processes: list[subprocess.Popen], *, api: bool = False) -> None:
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        if any(process.poll() is not None for process in processes):
            raise RuntimeError("Service exited. See .local/logs/*.stderr.log")
        try:
            if api:
                body = get_json(url)
                if body.get("status") == "ok" and body.get("db") == "up":
                    return
            else:
                with urlopen(url, timeout=3) as response:
                    if response.status == 200:
                        return
        except (URLError, TimeoutError, OSError):
            pass
        time.sleep(0.5)
    raise RuntimeError(f"Service not ready after 90s: {url}; see .local/logs")


def service_commands(args: argparse.Namespace) -> list[tuple[str, list[str], Path]]:
    commands = [
        ("api", [str(VENV_PY), "-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1",
                 "--port", str(args.api_port)], ROOT),
        ("frontend", ["node", str(ROOT / "frontend/node_modules/vite/bin/vite.js"),
                      "preview", "--host", "127.0.0.1", "--port", str(args.web_port),
                      "--strictPort"], ROOT / "frontend"),
    ]
    if not getattr(args, "skip_macro", False):
        commands.append(("macro", [str(VENV_PY), "-m", "backend.jobs.macro_worker"], ROOT))
    return commands


def service_environment(name: str, env: dict[str, str]) -> dict[str, str]:
    if name == "macro":
        return {**env, "WEALTHSAIL_ENV_FILE": str(LOCAL / "macro.env")}
    return {key:value for key,value in env.items() if key not in {"WECOM_OPPORTUNITY_WEBHOOK_URL","OPPORTUNITY_TENANT_ID","OPPORTUNITY_USER_ID"}}


def serve(args: argparse.Namespace, env: dict[str, str]) -> None:
    from scripts.local_processes import read_manifest, record_services
    env.update({"WEALTHSAIL_ENV_FILE": str(LOCAL / "api.env"),
                "WS_LOCAL_API_TARGET": f"http://127.0.0.1:{args.api_port}",
                "VITE_API_BASE_URL": "/api"})
    commands = service_commands(args)
    processes: list[subprocess.Popen] = []
    roots = {}
    handles = []
    state = read_state()
    if not state:
        raise RuntimeError("Launcher ownership state is missing")
    run_id = state["run_id"]

    def record_children():
        previous = read_manifest(LOCAL / "services.json", ROOT)
        candidates = set(roots) | {row["ProcessId"] for row in (previous or {}).get("children", [])}
        record_services(LOCAL / "services.json", ROOT, run_id, roots,
                        service_process_inventory(sorted(candidates), env))
    try:
        for name, command, directory in commands:
            stdout = (LOCAL / f"logs/{name}.stdout.log").open("w", encoding="utf-8")
            stderr = (LOCAL / f"logs/{name}.stderr.log").open("w", encoding="utf-8")
            handles.extend([stdout, stderr])
            processes.append(subprocess.Popen(command, cwd=directory, env=service_environment(name, env),
                                               stdout=stdout, stderr=stderr,
                                               creationflags=subprocess.CREATE_NO_WINDOW))
            roots[processes[-1].pid] = name
            record_children()
        web = f"http://127.0.0.1:{args.web_port}"
        wait_ready(f"http://127.0.0.1:{args.api_port}/healthz", processes, api=True)
        wait_ready(web, processes)
        record_children()
        # Exercise the compiled frontend's proxy and the actual login/portfolio API.
        # The initial password can be changed by the user, so later runs do not
        # require it. Seed correctness is verified on the first launch only.
        if not (LOCAL / "login-verified").exists():
            login = get_json(web + "/api/v1/auth/login", data={"email": EMAIL, "password": PASSWORD})
            get_json(web + "/api/v1/portfolio/summary", token=login["data"]["accessToken"])
            (LOCAL / "login-verified").touch()
        state = read_state()
        if state:
            state["phase"] = "ready"
            write_state(state)
        print(f"\nWeb: {web}\nAPI: http://127.0.0.1:{args.api_port}/docs"
              f"\nAccount: {EMAIL}\nInitial password: {PASSWORD}"
              "\nMacro data: background startup check and daily 06:30 refresh (unless -SkipMacro)."
              "\nPress Ctrl+C to stop local services/database; local data is retained.", flush=True)
        if not args.no_browser:
            webbrowser.open(web)
        next_record = time.monotonic() + 10
        while all(process.poll() is None for process in processes):
            if stop_requested():
                return
            if time.monotonic() >= next_record:
                record_children()
                next_record = time.monotonic() + 10
            time.sleep(1)
        raise RuntimeError("Service stopped unexpectedly; see .local/logs")
    finally:
        try:
            try:
                remaining = cleanup_service_records(env)
            finally:
                # Manifest I/O failure cannot skip the Popen-owned fallback.
                for process in reversed(processes):
                    try:
                        if process.poll() is None:
                            terminate_service_process(process.pid, env)
                        process.wait(timeout=10)
                    except (OSError, subprocess.SubprocessError):
                        pass
            if remaining:
                remaining = cleanup_service_records(env)
            if remaining:
                raise RuntimeError("Managed service cleanup incomplete; records retained. Use -Action Stop")
        finally:
            for handle in handles:
                handle.close()


def prepare_runtime(args: argparse.Namespace, env: dict[str, str]) -> tuple[dict[str, str], Path]:
    global VENV_PY
    from scripts.local_postgres import postgres_bin

    require_tools(env)
    check_ports([args.db_port, args.api_port, args.web_port])
    check_stop_request()
    settings = configure(args)
    VENV_PY = select_python(env, skip_install=args.skip_install)
    print(f"Using Python environment: {VENV_PY.parent.parent}", flush=True)
    if not args.skip_install:
        print("Installing Python/frontend dependencies...", flush=True)
        run([str(VENV_PY), "-m", "pip", "install", "--no-cache-dir", "-r", "requirements.txt", "tzdata"], env)
        check_stop_request()
        # Keep an existing store/modules installation to avoid a second copy.
        command = ["pnpm.cmd", "install", "--frozen-lockfile"]
        if not (ROOT / "frontend/node_modules").exists():
            command.extend(["--store-dir", str(LOCAL / "cache/pnpm")])
        run(command, {**env, "CI": "true"}, cwd=ROOT / "frontend")
        check_stop_request()
    run([str(VENV_PY), "-c", "from zoneinfo import ZoneInfo; ZoneInfo('Asia/Shanghai')"], env)
    print("Building frontend...", flush=True)
    run(["pnpm.cmd", "build"], env, cwd=ROOT / "frontend")
    check_stop_request()
    binary_dir = postgres_bin(LOCAL, args.postgres_bin, env)
    check_stop_request()
    return settings, binary_dir


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--action", choices=("start", "stop", "status", "refreshmacro", "refreshresearch"), default="start")
    parser.add_argument("--research-request-key")
    parser.add_argument("--postgres-bin", type=Path)
    parser.add_argument("--db-port", type=int, default=55432)
    parser.add_argument("--api-port", type=int, default=8000)
    parser.add_argument("--web-port", type=int, default=5173)
    parser.add_argument("--skip-install", action="store_true")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--skip-macro", action="store_true")
    parser.add_argument("--initialize", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    for directory in ("tmp", "cache", "logs"):
        (LOCAL / directory).mkdir(parents=True, exist_ok=True)
    env = child_environment(dict(os.environ), LOCAL)
    env.update({"WS_LOCAL_API_TARGET": f"http://127.0.0.1:{args.api_port}",
                "VITE_API_BASE_URL": "/api"})
    if args.action == "refreshmacro":
        refresh_local_macro(args, env)
        return
    if args.action == "refreshresearch":
        refresh_local_research(args, env)
        return
    if args.action != "start":
        runtime_action(args.action, env, args.postgres_bin)
        return
    if args.initialize:
        import asyncio
        settings = json.loads((LOCAL / "secrets.json").read_text("utf-8"))
        dsn = f"postgresql://postgres:{settings['postgres_password']}@127.0.0.1:{args.db_port}/wealthsail_local?sslmode=disable"
        asyncio.run(initialize_database(dsn, settings["api_password"], settings.get("worker_password")))
        return
    if os.name != "nt" or sys.version_info[:2] != (3, 12):
        raise RuntimeError("Use Windows and Python 3.12 via start-local.ps1")
    from scripts.local_postgres import start_database, stop_database

    # One supervisor per checkout; releasing this OS lock also handles crashes.
    with launcher_lock(LOCAL / "launcher.lock"):
        # Keep recovery proof intact until every recorded identity is gone.
        if remaining_service_records(env):
            raise RuntimeError("Managed child services remain; use -Action Stop before Start")
        (LOCAL / "services.json").unlink(missing_ok=True)
        binary_dir = args.postgres_bin.resolve() if args.postgres_bin else LOCAL / "postgres/pgsql/bin"
        run_id = secrets.token_hex(16)
        database_started = False
        try:
            state = {"pid": os.getpid(), "created": process_info(os.getpid(), env)["created"],
                     "run_id": run_id, "phase": "preparing", "postgres_bin": str(binary_dir),
                     "web_port": args.web_port, "api_port": args.api_port, "db_port": args.db_port,
                     "skip_macro": args.skip_macro}
            write_state(state)
            settings, binary_dir = prepare_runtime(args, env)
            state["phase"] = "starting"
            write_state(state)
            database_started = True
            start_database(binary_dir, LOCAL, args.db_port, settings["postgres_password"], env)
            check_stop_request()
            print("Applying local database migrations...", flush=True)
            run([str(VENV_PY), str(Path(__file__).resolve()), "--initialize", "--db-port", str(args.db_port)], env)
            if not stop_requested():
                serve(args, env)
        finally:
            try:
                if database_started:
                    stop_database(binary_dir, LOCAL, env)
            finally:
                current = read_state()
                if current and current.get("run_id") == run_id:
                    if (LOCAL / "services.json").exists():
                        current["phase"] = "cleanup_failed"
                        write_state(current)
                    else:
                        (LOCAL / "run.json").unlink(missing_ok=True)
                flag = LOCAL / "stop-request"
                if flag.exists() and flag.read_text("utf-8") == run_id:
                    flag.unlink()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped. Local database data retained.")
    except (RuntimeError, ValueError, subprocess.CalledProcessError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
