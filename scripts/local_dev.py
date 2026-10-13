"""Build and run the real Windows stack against portable PostgreSQL.

Invoked by start-local.ps1. Standard-library bootstrap; runtime imports are lazy.
All children run with a filtered environment and project-local temp/cache paths.
"""

from __future__ import annotations

import argparse
import base64
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
        "processor_architecture",
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
                raise RuntimeError(f"Port {port} occupied; change -ApiPort/-WebPort") from exc


def configure(args: argparse.Namespace) -> dict[str, str]:
    settings_path = LOCAL / "secrets.json"
    if settings_path.exists():
        settings = json.loads(settings_path.read_text("utf-8"))
    else:
        settings = {key: secrets.token_hex(32) for key in (
            "postgres_password", "api_password", "jwt", "refresh", "reset", "binding")}
        settings["routing"] = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
        settings_path.write_text(json.dumps(settings, indent=2), encoding="utf-8")
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
    }
    (LOCAL / "api.env").write_text(
        "\n".join(f"{key}={value}" for key, value in api_settings.items()) + "\n",
        encoding="utf-8")
    return settings


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
    code = (f"Get-CimInstance Win32_Process -Filter 'ProcessId = {int(pid)}' | "
            "Select-Object ProcessId,Name,@{Name='created';Expression={"
            "$_.CreationDate.ToUniversalTime().ToString('o')}} | ConvertTo-Json -Compress")
    output = run(["powershell.exe", "-NoProfile", "-Command", code], env, capture=True)
    return json.loads(output) if output and output != "null" else None


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


def runtime_action(action: str, env: dict[str, str], supplied_bin: Path | None) -> None:
    from scripts.local_postgres import stop_database

    state = read_state()
    alive = bool(state and state_alive(state, env))
    if action == "status":
        if alive:
            print(f"{state['phase']}: http://127.0.0.1:{state['web_port']} (PID {state['pid']})")
        else:
            print("Not running")
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
    binary_dir = supplied_bin or (Path(state["postgres_bin"]) if state else LOCAL / "postgres/pgsql/bin")
    if (binary_dir / "pg_ctl.exe").exists():
        stop_database(binary_dir, LOCAL, env)
    print("Not running; any remaining managed PostgreSQL has been stopped.")


async def initialize_database(dsn: str, api_password: str) -> None:
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


def serve(args: argparse.Namespace, env: dict[str, str]) -> None:
    env.update({"WEALTHSAIL_ENV_FILE": str(LOCAL / "api.env"),
                "WS_LOCAL_API_TARGET": f"http://127.0.0.1:{args.api_port}",
                "VITE_API_BASE_URL": "/api"})
    commands = [
        ("api", [str(VENV_PY), "-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1",
                 "--port", str(args.api_port)], ROOT),
        ("frontend", ["node", str(ROOT / "frontend/node_modules/vite/bin/vite.js"),
                      "preview", "--host", "127.0.0.1", "--port", str(args.web_port),
                      "--strictPort"], ROOT / "frontend"),
    ]
    processes: list[subprocess.Popen] = []
    handles = []
    try:
        for name, command, directory in commands:
            stdout = (LOCAL / f"logs/{name}.stdout.log").open("w", encoding="utf-8")
            stderr = (LOCAL / f"logs/{name}.stderr.log").open("w", encoding="utf-8")
            handles.extend([stdout, stderr])
            processes.append(subprocess.Popen(command, cwd=directory, env=env,
                                               stdout=stdout, stderr=stderr,
                                               creationflags=subprocess.CREATE_NO_WINDOW))
        web = f"http://127.0.0.1:{args.web_port}"
        wait_ready(f"http://127.0.0.1:{args.api_port}/healthz", processes, api=True)
        wait_ready(web, processes)
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
              "\nPress Ctrl+C to stop API/frontend/database; local data is retained.", flush=True)
        if not args.no_browser:
            webbrowser.open(web)
        while all(process.poll() is None for process in processes):
            if stop_requested():
                return
            time.sleep(1)
        raise RuntimeError("Service stopped unexpectedly; see .local/logs")
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                # Windows venv python.exe is a redirector with a child process.
                # Stop our Popen-owned tree so the actual API cannot be orphaned.
                subprocess.run(["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                               env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               creationflags=subprocess.CREATE_NO_WINDOW, check=False)
                process.wait(timeout=10)
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
    parser.add_argument("--action", choices=("start", "stop", "status"), default="start")
    parser.add_argument("--postgres-bin", type=Path)
    parser.add_argument("--db-port", type=int, default=55432)
    parser.add_argument("--api-port", type=int, default=8000)
    parser.add_argument("--web-port", type=int, default=5173)
    parser.add_argument("--skip-install", action="store_true")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--initialize", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    for directory in ("tmp", "cache", "logs"):
        (LOCAL / directory).mkdir(parents=True, exist_ok=True)
    env = child_environment(dict(os.environ), LOCAL)
    env.update({"WS_LOCAL_API_TARGET": f"http://127.0.0.1:{args.api_port}",
                "VITE_API_BASE_URL": "/api"})
    if args.action != "start":
        runtime_action(args.action, env, args.postgres_bin)
        return
    if args.initialize:
        import asyncio
        settings = json.loads((LOCAL / "secrets.json").read_text("utf-8"))
        dsn = f"postgresql://postgres:{settings['postgres_password']}@127.0.0.1:{args.db_port}/wealthsail_local?sslmode=disable"
        asyncio.run(initialize_database(dsn, settings["api_password"]))
        return
    if os.name != "nt" or sys.version_info[:2] != (3, 12):
        raise RuntimeError("Use Windows and Python 3.12 via start-local.ps1")
    from scripts.local_postgres import start_database, stop_database
    import msvcrt

    # One supervisor per checkout; releasing this OS lock also handles crashes.
    lock = (LOCAL / "launcher.lock").open("a+b")
    lock.seek(0)
    if lock.read(1) == b"":
        lock.write(b"0")
        lock.flush()
    lock.seek(0)
    try:
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        lock.close()
        raise RuntimeError("Already running; use -Action Status or -Action Stop") from None
    binary_dir = args.postgres_bin.resolve() if args.postgres_bin else LOCAL / "postgres/pgsql/bin"
    state = {"pid": os.getpid(), "created": process_info(os.getpid(), env)["created"],
             "run_id": secrets.token_hex(16), "phase": "preparing", "postgres_bin": str(binary_dir),
             "web_port": args.web_port, "api_port": args.api_port, "db_port": args.db_port}
    write_state(state)
    database_started = False
    try:
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
            (LOCAL / "run.json").unlink(missing_ok=True)
            (LOCAL / "stop-request").unlink(missing_ok=True)
            lock.close()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped. Local database data retained.")
    except (RuntimeError, ValueError, subprocess.CalledProcessError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
