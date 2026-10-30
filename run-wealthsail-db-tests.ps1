#Requires -Version 5.1
<#
.SYNOPSIS
  One-click WealthSail AI-usage integration tests on the EXISTING portable Windows PostgreSQL.
.DESCRIPTION
  Uses .local/postgres/pgsql/bin and .local/pgdata from start-local.ps1.
  Starts only PostgreSQL 16, never Docker, frontend, API or Worker.
  Creates/updates ONLY the dedicated ai_usage_isolated_test database; never drops a database.
  Runs static migration contract tests, then real PostgreSQL AI-usage integration tests.
  Saves a timestamped summary/log under .local/logs and reports pass/fail/skip counts.
.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File .\run-wealthsail-db-tests.ps1
#>
[CmdletBinding()]
param(
    [ValidateRange(1024, 65535)][int]$DbPort = 55432,
    [switch]$KeepPostgresRunning
)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
Push-Location -LiteralPath $root
try {
    $candidates = @(
        @{ Command = (Join-Path $root '.venv\Scripts\python.exe'); Args = @() },
        @{ Command = (Join-Path $root '.venv-local\Scripts\python.exe'); Args = @() },
        @{ Command = 'py.exe'; Args = @('-3.12') }
    )
    $selected = $null
    foreach ($candidate in $candidates) {
        $found = if ([System.IO.Path]::IsPathRooted($candidate.Command)) {
            Test-Path -LiteralPath $candidate.Command -PathType Leaf
        } else {
            [bool](Get-Command $candidate.Command -ErrorAction SilentlyContinue)
        }
        if (-not $found) { continue }
        # PowerShell 5.1 mangles nested quotes inside python -c arguments.
        # Use the version flag, which contains no embedded Python code.
        try {
            $versionLines = & $candidate.Command @($candidate.Args) --version 2>&1
            if ($LASTEXITCODE -ne 0) { continue }
        }
        catch { continue }
        $version = ($versionLines | Out-String).Trim()
        if ($version -match '^Python 3\.12(?:\.|$)') {
            $selected = $candidate
            break
        }
    }
    if ($null -eq $selected) {
        throw 'Python 3.12 not found. Reuse the Python 3.12 environment from start-local.ps1.'
    }

    # This Python is embedded here so there is exactly ONE file to download/copy.
    $runner = @'
from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import quote

ROOT = Path.cwd().resolve()
# Temp runner lives under .local/tmp, so add repository root explicitly for imports.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
LOCAL = ROOT / '.local'
TEST_DB = 'ai_usage_isolated_test'
PORT = int(sys.argv[1])
KEEP_RUNNING = sys.argv[2].lower() == 'true'
LOG_DIR = LOCAL / 'logs'
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG = LOG_DIR / ('ai-usage-db-tests-' + dt.datetime.now().strftime('%Y%m%d-%H%M%S') + '.log')
RESULT = LOG_DIR / 'ai-usage-db-tests-latest.json'
started_by_us = False
status = {'success': False, 'database': TEST_DB, 'port': PORT, 'phases': {}, 'log': str(LOG)}


def say(message: str):
    msg = str(message)
    print(msg, flush=True)
    with LOG.open('a', encoding='utf-8') as file:
        file.write(msg + '\n')


def fail(message: str):
    raise RuntimeError(message)


def run_phase(label: str, args: list[str], env: dict[str, str] | None = None):
    say('\n=== ' + label + ' ===')
    process = subprocess.Popen(
        [sys.executable, *args], cwd=str(ROOT), env=env or os.environ.copy(),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        encoding='utf-8', errors='replace', bufsize=1,
    )
    lines = []
    assert process.stdout is not None
    with process.stdout as output:
        for line in output:
            line = line.rstrip('\r\n')
            lines.append(line)
            say(line)
    code = process.wait()
    summary = next((line.strip() for line in reversed(lines)
                    if re.search(r'\b(?:\d+\s+(?:passed|failed|skipped|error|errors))\b', line)), '')
    counts = {key: int(val) for val, key in re.findall(
        r'(\d+)\s+(passed|failed|skipped|error|errors)\b', summary
    )}
    phase = {'exit_code': code, 'summary': summary or '(no pytest summary)',
             'passed': counts.get('passed', 0), 'failed': counts.get('failed', 0),
             'skipped': counts.get('skipped', 0),
             'errors': counts.get('errors', 0) + counts.get('error', 0)}
    status['phases'][label] = phase
    if code:
        fail(f'{label}: FAILED (exit code {code}). Check log: {LOG}')
    if not summary:
        fail(f'{label}: pytest produced no recognizable summary; cannot claim success.')
    if label == 'PostgreSQL integration' and (phase['skipped'] or phase['passed'] == 0):
        fail('Database tests must run, not SKIP. Inspect pytest output and environment.')
    say('RESULT: ' + summary)


def check_requirements():
    missing = [module for module in ('pytest', 'pytest_asyncio', 'asyncpg')
               if importlib.util.find_spec(module) is None]
    if missing:
        say('Installing missing *small* Python test dependencies: ' + ', '.join(missing))
        names = {'pytest': 'pytest==8.3.4', 'pytest_asyncio': 'pytest-asyncio==0.24.0',
                 'asyncpg': 'asyncpg==0.30.0'}
        result = subprocess.run([sys.executable, '-m', 'pip', 'install', '--no-cache-dir',
                                 *[names[module] for module in missing]],
                                cwd=str(ROOT), check=False)
        if result.returncode:
            fail('pip install failed. No database migration has been attempted.')
    # Confirm the plugin loads: otherwise async tests might be skipped or raise a warning.
    from importlib.metadata import version
    say('Python: ' + sys.version.split()[0] + '; pytest: ' + version('pytest') +
        '; pytest-asyncio: ' + version('pytest-asyncio'))


def check_files():
    required = [
        ROOT / 'scripts' / 'migrate.py',
        ROOT / 'migrations' / '093_fix_ai_usage_overlap_json_and_role.sql',
        ROOT / 'migrations' / '094_harden_ai_usage_rls.sql',
        ROOT / 'tests' / 'unit' / 'test_ai_usage_migration_093.py',
        ROOT / 'tests' / 'unit' / 'test_ai_usage_migration_094.py',
        ROOT / 'tests' / 'unit' / 'test_ai_usage_database.py',
        LOCAL / 'postgres' / 'pgsql' / 'bin' / 'pg_ctl.exe',
        LOCAL / 'pgdata' / 'PG_VERSION',
        LOCAL / 'secrets.json',
    ]
    missing = [str(path.relative_to(ROOT)) for path in required if not path.is_file()]
    if missing:
        fail('Required existing local files missing: ' + ', '.join(missing) +
             '. Run start-local.ps1 at least once; save migration 094 and its test first.')
    version = (LOCAL / 'pgdata' / 'PG_VERSION').read_text(encoding='utf-8').strip()
    if version != '16':
        fail(f'This portable cluster uses PostgreSQL {version}, expected 16. Refusing to change it.')
    old = (ROOT / 'migrations' / '093_fix_ai_usage_overlap_json_and_role.sql').read_text(encoding='utf-8')
    digest = hashlib.sha256(old.encode('utf-8')).hexdigest()
    if not digest.startswith('4b88cc2bcabb'):
        fail('Migration 093 differs from NAS-applied 0047741 checksum. Restore the original 093 first.')
    say('Migration 093 checksum matches NAS: ' + digest[:12])


def start_existing_pg(secret: dict):
    global started_by_us
    from scripts.local_postgres import database_running, start_database
    bin_path = LOCAL / 'postgres' / 'pgsql' / 'bin'
    if database_running(bin_path, LOCAL, os.environ.copy()):
        say('Reusing already-running .local/pgdata PostgreSQL (no extra process).')
    else:
        say('Starting EXISTING portable PostgreSQL; no download, no new data directory.')
        start_database(bin_path, LOCAL, PORT, secret['postgres_password'], os.environ.copy())
        started_by_us = True
    import subprocess as sp
    pid_path = LOCAL / 'pgdata' / 'postmaster.pid'
    actual_port = int(pid_path.read_text(encoding='utf-8').splitlines()[3])
    if actual_port != PORT:
        fail(f'Existing cluster is on port {actual_port}, not requested {PORT}. Retry -DbPort {actual_port}.')
    say(f'PostgreSQL ready at 127.0.0.1:{PORT}; owned data directory unchanged.')


async def prepare_database(admin_dsn: str):
    import asyncpg
    from scripts.migrate import load_migrations, migrate
    management = await asyncpg.connect(admin_dsn)
    try:
        db_exists = await management.fetchval(
            'SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname=$1)', TEST_DB)
        if not db_exists:
            say('Creating dedicated test database ' + TEST_DB + ' (no existing DB touched).')
            await management.execute('CREATE DATABASE ai_usage_isolated_test TEMPLATE template0 ENCODING \'UTF8\'')
        else:
            say('Reusing dedicated test database ' + TEST_DB + '.')
    finally:
        await management.close()

    dsn = admin_dsn.rsplit('/', 1)[0] + '/' + TEST_DB
    conn = await asyncpg.connect(dsn)
    try:
        correct = await conn.fetchval('SELECT current_database() = $1', TEST_DB)
        if not correct:
            fail('Safety check failed: unexpected current database.')
        has_ledger = await conn.fetchval(
            "SELECT to_regclass('public.wealthsail_schema_migrations') IS NOT NULL")
        if not has_ledger:
            nonempty = await conn.fetchval('''
                SELECT EXISTS (
                    SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                    WHERE n.nspname NOT LIKE 'pg_%'
                      AND n.nspname NOT IN ('information_schema')
                      AND c.relkind IN ('r','p','v','m','S','f')
                )''')
            if nonempty:
                fail('Test DB has objects but no migration ledger; refusing to alter it automatically.')
    finally:
        await conn.close()
    say('Applying/checking forward-only migrations 001 through 094 on TEST DB only...')
    # This migration runner checks historical SHA256 and reads back permissions.
    capture = io.StringIO()
    try:
        with contextlib.redirect_stdout(capture):
            await migrate(dsn, load_migrations(ROOT / 'migrations'), dry_run=False)
    except Exception:
        for line in capture.getvalue().splitlines()[-15:]:
            say('migration: ' + line)
        raise
    changes = [line for line in capture.getvalue().splitlines()
               if line.startswith(('APPLIED ', 'READBACK OK:'))]
    for line in changes:
        say(line)
    status['migrations_applied'] = len([line for line in changes if line.startswith('APPLIED ')])
    verify = await asyncpg.connect(dsn)
    try:
        latest = await verify.fetchval('SELECT MAX(version) FROM public.wealthsail_schema_migrations')
        if latest != 94:
            fail(f'Expected Migration 094 as latest, got {latest}; inspect source migration directory.')
        say('Migration ledger latest version: ' + str(latest))
    finally:
        await verify.close()
    return dsn


def main():
    say('WealthSail portable PostgreSQL AI-usage integration tests')
    say('Project: ' + str(ROOT))
    say('Log: ' + str(LOG))
    check_files()
    check_requirements()
    run_phase('Static migration contracts', ['-m', 'pytest',
        'tests/unit/test_ai_usage_migration_093.py',
        'tests/unit/test_ai_usage_migration_094.py', '-q', '-rs', '--tb=short'])

    with (LOCAL / 'secrets.json').open('r', encoding='utf-8') as fp:
        secrets = json.load(fp)
    pg_password = secrets.get('postgres_password')
    if not isinstance(pg_password, str) or not pg_password:
        fail('.local/secrets.json lacks the existing postgres_password; will not reset credentials.')
    start_existing_pg(secrets)
    admin_dsn = (f'postgresql://postgres:{quote(pg_password, safe="")}@127.0.0.1:{PORT}/postgres')
    test_dsn = asyncio.run(prepare_database(admin_dsn))
    env = os.environ.copy()
    env['AI_USAGE_TEST_DATABASE_URL'] = test_dsn
    env['PYTHONUTF8'] = '1'
    run_phase('PostgreSQL integration',
              ['-m', 'pytest', 'tests/unit/test_ai_usage_database.py', '-q', '-rs', '--tb=short'], env)
    status['success'] = True
    say('\nSUCCESS: SQL contracts and real PostgreSQL integration tests passed.')


try:
    main()
except Exception as exc:
    status['error'] = str(exc)
    say('\nFAILED: ' + str(exc))
finally:
    status['finished_at'] = dt.datetime.now().isoformat(timespec='seconds')
    # Never save credentials/DSNs to the result JSON; the log is test-only.
    RESULT.write_text(json.dumps(status, indent=2, ensure_ascii=False), encoding='utf-8')
    say('Summary: ' + str(RESULT))
    say('Full log: ' + str(LOG))
    if started_by_us:
        if KEEP_RUNNING:
            say('PostgreSQL was started for tests and left running (-KeepPostgresRunning).')
        else:
            try:
                from scripts.local_postgres import stop_database
                stop_database(LOCAL / 'postgres' / 'pgsql' / 'bin', LOCAL, os.environ.copy())
                say('PostgreSQL was started for tests and automatically stopped; data retained.')
            except Exception as stop_exc:
                say('WARNING: Could not automatically stop PostgreSQL: ' + str(stop_exc))

sys.exit(0 if status['success'] else 1)
'@

    # Never pass the embedded runner through python -c on Windows PowerShell 5.1.
    # Write a short-lived UTF-8 .py file instead, then remove it even on failure.
    $tmpDir = Join-Path $root '.local\tmp'
    [void][System.IO.Directory]::CreateDirectory($tmpDir)
    $runnerFile = Join-Path $tmpDir ("wealthsail-ai-usage-runner-" + [guid]::NewGuid().ToString('N') + '.py')
    [System.IO.File]::WriteAllText($runnerFile, $runner, [System.Text.UTF8Encoding]::new($false))
    Write-Host "Using Python: $($selected.Command)"
    $code = 1
    try {
        # The runner logs and summarizes Python errors itself. Keep PS 5.1 from
        # converting a native application's stderr into a terminating exception.
        $savedPreference = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            & $selected.Command @($selected.Args) $runnerFile "$DbPort" "$($KeepPostgresRunning.IsPresent)"
            $code = $LASTEXITCODE
        }
        finally {
            $ErrorActionPreference = $savedPreference
        }
    }
    finally {
        Remove-Item -LiteralPath $runnerFile -Force -ErrorAction SilentlyContinue
    }
    if ($code -ne 0) {
        Write-Host "`nOne-click integration tests FAILED. Inspect .local\logs\ai-usage-db-tests-latest.json" -ForegroundColor Red
    } else {
        Write-Host "`nOne-click integration tests PASSED." -ForegroundColor Green
    }
    exit $code
}
finally {
    Pop-Location
}
