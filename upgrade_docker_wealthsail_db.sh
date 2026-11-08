#!/usr/bin/env bash
# WealthSail NAS production upgrade: build -> migration plan -> stop writers ->
# verified PostgreSQL backup -> forward-only SQL migrations -> deploy -> verify.
#
# INSTALL (WealthSail source root, NEXT TO build_docker_wealthsail.sh):
#   cp upgrade_docker_wealthsail_db.sh /path/to/WealthSail/
#   chmod +x upgrade_docker_wealthsail_db.sh
#
# USAGE:
#   bash upgrade_docker_wealthsail_db.sh --check   # Read-only preflight + SQL PLAN/SKIP/DRIFT
#   sudo bash upgrade_docker_wealthsail_db.sh --apply
#   sudo bash upgrade_docker_wealthsail_db.sh --resume-readback
#
# AUTOMATED --apply ORDER:
#   1. Check project, database, and local SQL files vs migration ledger (read-only).
#   2. Build backend/frontend images; running services stay online.
#   3. Run a migration DRY RUN using the migration administrator.
#   4. If NO pending SQL, deploy only (no DB downtime or backup needed).
#   5. Otherwise confirm, stop ONLY worker/api, and back up PostgreSQL.
#   6. Apply forward-only scripts/migrate.py, require READBACK OK and ledger match.
#   7. Deploy images with build_docker_wealthsail.sh --deploy-only; verify services.
#
# IMPORTANT:
#   - Uses existing dedicated NAS 'nas-compose ws' overlays and project name.
#   - Assumes the NAS shared PostgreSQL container is named 'postgres' and that
#     local `pg_dump -U admin -d wealthsail` works inside it.
#   - SQL administrator credential comes from a protected root-owned config
#     file outside the source checkout and outside all application mounts.
#   - Password is NOT injected into Compose service env, Docker args, or logs;
#     script reads it privately and streams to one-off migration via stdin.
#   - Never runs initial installation, dev seeds, down -v, or volume prune.
#   - Migration failures are fail-closed: API/worker remain STOPPED for review.
#     Do not blindly restore an old image against a partly migrated database.
#   - The backup covers the PostgreSQL wealthsail database ONLY, not avatars,
#     auth volumes, app secrets, or other PostgreSQL databases.
#   - Requires interactive SSH terminal for --apply confirmation. Make a
#     separate off-NAS copy of backups and test isolated restores periodically.
#
# ONE-TIME PRIVATE CREDENTIAL SETUP (on NAS; NEVER store in source repository):
#   sudo install -d -o root -g root -m 0700 \
#       /data_n002/data/udata/real/15221151691/docker/wealth-sail/private
#   sudo touch /data_n002/data/udata/real/15221151691/docker/wealth-sail/private/migration-admin.env
#   sudo chown root:root /data_n002/data/udata/real/15221151691/docker/wealth-sail/private/migration-admin.env
#   sudo chmod 0600 /data_n002/data/udata/real/15221151691/docker/wealth-sail/private/migration-admin.env
#   sudo nano /data_n002/data/udata/real/15221151691/docker/wealth-sail/private/migration-admin.env
#   Contents (unquoted values; literal $, #, = and & are supported):
#       PG_ADMIN_USER=admin
#       PG_ADMIN_PASSWORD=<actual-secret>
#   Do NOT `source` this env file. Password remains in plaintext on NAS.
#
# OVERRIDE only if the already-installed NAS wrapper lives elsewhere:
#   NAS_TOOLS=/absolute/path/to/tools sudo -E bash upgrade_docker_wealthsail_db.sh --apply

set -Eeuo pipefail
umask 077

NAS_TOOLS="${NAS_TOOLS:-/data_n002/data/udata/real/15221151691/docker/cloudflared/tools}"
COMPOSE="$NAS_TOOLS/nas-compose"
DEPLOY_JSON="$NAS_TOOLS/deploy.json"
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
BUILD_SCRIPT="$ROOT/build_docker_wealthsail.sh"
EXPECTED_DB='postgres:5432/wealthsail'
EXPECTED_PG_CONTAINER='postgres'
EXPECTED_PG_DB='wealthsail'
EXPECTED_PG_USER='admin'
BACKUP_DIR='/data_n002/data/udata/real/15221151691/docker/wealth-sail/backups/postgres'
ADMIN_ENV_FILE="${DB_ADMIN_ENV_FILE:-/data_n002/data/udata/real/15221151691/docker/wealth-sail/private/migration-admin.env}"

MODE=''
LOCAL_PENDING_COUNT=''
PLAN_LOG=''
APPLY_LOG=''
PG_ADMIN_PASSWORD=''
export -n PG_ADMIN_PASSWORD
DOWNTIME_STARTED=0
MIGRATION_ATTEMPTED=0
MIGRATION_SUCCESS=0
BACKUP_FILE=''

usage() {
  cat <<'USAGE'
Usage: bash upgrade_docker_wealthsail_db.sh [--check | --apply | --resume-readback]

  --check   Validate deployment and READ-ONLY compare migrations/*.sql to the
            live PostgreSQL migration ledger (PLAN pending / SKIP applied /
            DRIFT mismatched). Print counts or "NO PENDING SQL MIGRATIONS".
            Does NOT build, stop containers, back up, or execute SQL migrations.
  --apply   Build -> real migration dry-run -> if pending: confirm/stop writers/
            verified DB backup/migrate -> deploy -> verify. Without pending SQL,
            deploy code only (no database downtime or backup).
            Uses the protected admin credential file; no password prompt.
  --resume-readback
            Recover after all SQL committed but final readback failed. Requires
            API/Worker STOPPED, matching ledger/checksums and NO pending SQL.
            Build -> db-init dry-run -> read-only readback -> deploy only after
            every migration SKIP and READBACK OK. No SQL or ledger changes.
            Uses the existing protected admin file; no SSH TTY required.
  --help    Display these instructions.

Recommended for changes with new SQL (e.g. migrations/091_ai_usage.sql):
  1. Ensure no background tasks need to finish during the maintenance window.
  2. bash upgrade_docker_wealthsail_db.sh --check
  3. sudo bash upgrade_docker_wealthsail_db.sh --apply
  4. Verify web login, AI usage and worker logs after deployment.

Private credential file (NOT in Git or app env; owner root:root, chmod 600):
  /data_n002/data/udata/real/15221151691/docker/wealth-sail/private/migration-admin.env
  PG_ADMIN_USER=admin
  PG_ADMIN_PASSWORD=<actual-secret>
Values are literal strings: NO shell expansion, NO surrounding quotes.
The parent directory must be root-owned, chmod 700. To change its location:
  sudo DB_ADMIN_ENV_FILE=/absolute/private/file bash upgrade_docker_wealthsail_db.sh --apply

For code-only updates with NO new SQL use build_docker_wealthsail.sh.

Production safety: No automatic DB rollback. On a failed migration, do NOT
restart old services without reviewing the failure and schema state.
--check checks SQL files and checksums, NOT complete schema correctness.
For stopped services after a failed readback, use --resume-readback instead.
Keep the original pre-migration backup; do not reconcile or reapply old SQL.
USAGE
}

fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
log() { printf '\n==> %s\n' "$*"; }

# Parse only two allowlisted keys. Never `source` the file or echo credentials.
# A root-owned chmod-700 directory and root-owned chmod-600 file are required.
read_admin_secret() {
  local mode="${1:-check}"
  sudo python3 - "$ADMIN_ENV_FILE" "$mode" <<'PY_ADMIN'
import os
import stat
import sys
from pathlib import Path

path = Path(sys.argv[1])
mode = sys.argv[2]
if mode not in {"check", "load"} or not path.is_absolute():
    raise SystemExit("Invalid private credential file or mode")
parent = path.parent
try:
    p = parent.lstat()
except OSError:
    raise SystemExit("Private credential directory missing") from None
if not stat.S_ISDIR(p.st_mode) or p.st_uid != 0 or stat.S_IMODE(p.st_mode) != 0o700:
    raise SystemExit("Private credential directory must be root-owned chmod 700 (not a symlink)")
try:
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
except OSError:
    raise SystemExit("Private credential file missing or not readable as a regular file") from None
with os.fdopen(fd, "r", encoding="utf-8", newline=None) as handle:
    f = os.fstat(handle.fileno())
    if not stat.S_ISREG(f.st_mode) or f.st_uid != 0 or stat.S_IMODE(f.st_mode) != 0o600:
        raise SystemExit("Credential file must be root-owned chmod 600 (not a symlink)")
    content = handle.read(8193)
if len(content) > 8192:
    raise SystemExit("Credential file too large")
values = {}
for line in content.splitlines():
    if not line.strip() or line.lstrip().startswith("#"):
        continue
    if "=" not in line:
        raise SystemExit("Invalid credential-file line (expected KEY=value)")
    key, value = line.split("=", 1)
    if key not in {"PG_ADMIN_USER", "PG_ADMIN_PASSWORD"} or key in values:
        raise SystemExit("Unknown or duplicate credential-file key")
    values[key] = value
if values.get("PG_ADMIN_USER") != "admin":
    raise SystemExit("PG_ADMIN_USER must match the verified NAS postgres admin role")
password = values.get("PG_ADMIN_PASSWORD", "")
if not password or "\x00" in password:
    raise SystemExit("PG_ADMIN_PASSWORD missing or malformed")
if (password.startswith("'") and password.endswith("'")) or (password.startswith('"') and password.endswith('"')):
    raise SystemExit("Write the password as a literal unquoted value in the credential file")
if mode == "load":
    sys.stdout.write(password)
else:
    print("OK: protected root-only DB admin credential file (secret redacted)")
PY_ADMIN
}

# Read-only local migration plan: compare the SAME migration filename/version
# and SHA-256 calculation as scripts/migrate.py against the live migration ledger.
# Uses the existing PostgreSQL container's local admin socket, which the main
# preflight already checked. No migration container, SQL execution, or changes
# to the migration ledger. Intentionally fail closed on checksum drift and
# database versions missing from the checked-out code.
show_migration_plan() {
  python3 - "$ROOT/migrations" "$EXPECTED_PG_CONTAINER" "$EXPECTED_PG_USER" "$EXPECTED_PG_DB" <<'PY_PLAN'
import hashlib
import re
import subprocess
import sys
from pathlib import Path

migration_dir = Path(sys.argv[1])
container, user, database = sys.argv[2:]
pattern = re.compile(r"^(\d{3})_[a-zA-Z0-9_.-]+\.sql$")
source = {}
for path in sorted(migration_dir.iterdir()):
    match = pattern.fullmatch(path.name) if path.is_file() else None
    if not match:
        continue
    version = int(match.group(1))
    if version in source:
        raise SystemExit(f"ERROR: duplicate source migration version {version:03d}")
    # Match scripts/migrate.py load_migrations(): read_text normalizes newlines.
    sql = path.read_text(encoding="utf-8")
    source[version] = (path.name, hashlib.sha256(sql.encode("utf-8")).hexdigest())
if not source:
    raise SystemExit("ERROR: no migrations/*.sql in this checkout")

sql = ("SELECT version, filename, btrim(checksum_sha256) "
       "FROM public.wealthsail_schema_migrations ORDER BY version")
cmd = ["sudo", "docker", "exec", container, "psql", "-X", "-q", "-A", "-t",
       "-F", "\t", "-v", "ON_ERROR_STOP=1", "-U", user, "-d", database,
       "-c", sql]
try:
    response = subprocess.run(cmd, check=True, capture_output=True, text=True)
except subprocess.CalledProcessError as exc:
    # Avoid echoing arbitrary database stderr into deployment logs.
    raise SystemExit("ERROR: cannot read production migration ledger with local admin; "
                     "check database connectivity and ledger schema") from None

applied = {}
for line in response.stdout.splitlines():
    if not line.strip():
        continue
    fields = line.split("\t")
    if len(fields) != 3:
        raise SystemExit("ERROR: malformed migration ledger result")
    version_str, filename, checksum = fields
    if not version_str.isdigit() or not re.fullmatch(r"[0-9a-f]{64}", checksum):
        raise SystemExit("ERROR: malformed migration ledger version/checksum")
    version = int(version_str)
    if version in applied:
        raise SystemExit(f"ERROR: duplicate database migration version {version:03d}")
    applied[version] = (filename, checksum)

pending = []
errors = []
for version, (name, checksum) in sorted(source.items()):
    db_row = applied.get(version)
    if db_row is None:
        pending.append(name)
        print(f"PLAN {name} sha256={checksum[:12]}")
    elif db_row[1] != checksum:
        errors.append(version)
        print(f"DRIFT {name}: ledger={db_row[1][:12]} source={checksum[:12]}")
    elif db_row[0] != name:
        errors.append(version)
        print(f"DRIFT {name}: ledger filename differs ({db_row[0]})")
for version, (name, _) in sorted(applied.items()):
    if version not in source:
        errors.append(version)
        print(f"MISSING_SOURCE {version:03d}: database has {name}")

applied_matches = sum(
    1 for version, (_, digest) in source.items()
    if version in applied and applied[version][1] == digest
)
print(f"SUMMARY: pending={len(pending)} applied={applied_matches} "
      f"source={len(source)} ledger={len(applied)}")
print(f"PENDING_COUNT={len(pending)}")
if not pending and not errors:
    print("NO PENDING SQL MIGRATIONS")
if errors:
    raise SystemExit("ERROR: migration ledger differs from this checkout; "
                     "do not automatically reconcile or deploy")
PY_PLAN
}

# No running API process is needed to verify the recovery target. Check all
# consumers, especially the one-off db-init service, without logging secrets.
verify_configured_database() {
  sudo "$COMPOSE" ws config --format json | python3 -c '
import json, sys
from urllib.parse import urlsplit
c = json.load(sys.stdin)
if c.get("name") != "wealthsail":
    raise SystemExit("Unexpected Compose project for migration recovery")
services = c.get("services", {})
for name, key in (("api", "DATABASE_URL"), ("worker", "WORKER_DATABASE_URL"), ("db-init", "DATABASE_URL")):
    service = services.get(name, {})
    try:
        url = urlsplit(service.get("environment", {}).get(key, ""))
        host = (url.hostname or "") + (":" + str(url.port) if url.port else "")
        valid = host + "/" + url.path.strip("/") == "postgres:5432/wealthsail"
    except (ValueError, AttributeError):
        valid = False
    if not valid:
        raise SystemExit("Configured database target mismatch for " + name)
    if service.get("image") != "wealthsail-backend:latest":
        raise SystemExit("Unexpected backend image for " + name)
    if name == "db-init" and service.get("environment", {}).get("POSTGRES_ADMIN_USER", "admin") != "admin":
        raise SystemExit("Unexpected migration administrator")
print("OK: API/Worker/db-init target is postgres:5432/wealthsail (credentials redacted)")
'
}

verify_stopped_writers() {
  local service ids cid running
  for service in api worker; do
    ids="$(sudo "$COMPOSE" ws ps -a -q "$service")" \
      || fail "Unable to inspect stopped service $service"
    while IFS= read -r cid; do
      [[ -n "$cid" ]] || continue
      running="$(sudo docker inspect -f '{{.State.Running}}' "$cid")" \
        || fail "Unable to inspect container for $service"
      [[ "$running" == false ]] || fail "Recovery requires $service stopped"
    done <<< "$ids"
  done
}

verify_skipped_migrations() {
  python3 - "$ROOT/migrations" "$1" <<'PY_SKIP'
import re
import sys
from pathlib import Path

expected = sorted(p.name for p in Path(sys.argv[1]).iterdir()
                  if p.is_file() and re.fullmatch(r"\d{3}_[a-zA-Z0-9_.-]+\.sql", p.name))
lines = Path(sys.argv[2]).read_text(encoding="utf-8").splitlines()
actual = []
for line in lines:
    if line.startswith(("PLAN ", "APPLIED ", "RECONCILE ", "DRIFT ")):
        raise SystemExit("Recovery must only SKIP existing migrations")
    if line.startswith("SKIP "):
        match = re.fullmatch(r"SKIP (\S+) \(already applied\)", line)
        if not match:
            raise SystemExit("Unexpected recovery migration result")
        actual.append(match.group(1))
if not expected or sorted(actual) != expected:
    raise SystemExit("Recovery SKIP results differ from the complete source migration list")
PY_SKIP
}

on_exit() {
  local status="$?"
  trap - EXIT
  PG_ADMIN_PASSWORD=''
  unset PG_ADMIN_PASSWORD
  if [[ -n "$PLAN_LOG" && -f "$PLAN_LOG" ]]; then rm -f -- "$PLAN_LOG"; fi
  if [[ -n "$APPLY_LOG" && -f "$APPLY_LOG" ]]; then rm -f -- "$APPLY_LOG"; fi
  if ((status != 0)); then
    printf '\nUPGRADE FAILED (exit %s).\n' "$status" >&2
    if [[ "$MODE" == resume ]] && ((DOWNTIME_STARTED)); then
      # Deployment may have started writers before a health check failed.
      # Never leave a failed recovery serving requests or restart old images.
      if ! sudo "$COMPOSE" ws stop worker api; then
        printf 'ERROR: unable to stop API/Worker; stop them manually before retrying.\n' >&2
      fi
    fi
    if ((DOWNTIME_STARTED)); then
      printf 'API/Worker may be stopped. Migration attempted=%s, readback passed=%s.\n' \
        "$MIGRATION_ATTEMPTED" "$MIGRATION_SUCCESS" >&2
      printf 'Review migration output and the DB backup; do NOT auto-rollback SQL.\n' >&2
      printf 'Service status: sudo "%s" ws ps\n' "$COMPOSE" >&2
      if [[ -n "$BACKUP_FILE" ]]; then
        printf 'Verified database backup: %s\n' "$BACKUP_FILE" >&2
      fi
    else
      printf 'The script did not request stopping the API/Worker.\n' >&2
    fi
  fi
  exit "$status"
}
trap on_exit EXIT
trap 'printf "Interrupted.\n" >&2; exit 130' INT
trap 'printf "Terminated.\n" >&2; exit 143' TERM

case "${1:-}" in
  --check) MODE='check' ;;
  --apply) MODE='apply' ;;
  --resume-readback) MODE='resume' ;;
  --help|-h|'') usage; exit 0 ;;
  *) usage >&2; fail "Unknown mode: $1" ;;
esac
[[ $# -eq 1 ]] || fail 'Choose exactly one mode: --check, --apply or --resume-readback'

[[ -x "$COMPOSE" && -f "$DEPLOY_JSON" ]] || fail "Missing nas-compose or deploy.json under $NAS_TOOLS"
[[ -f "$BUILD_SCRIPT" ]] || fail "Missing $BUILD_SCRIPT"
[[ -f "$ROOT/scripts/migrate.py" && -d "$ROOT/migrations" ]] || fail 'Migration entrypoint or SQL directory missing in this checkout'
[[ -f "$ROOT/backend/Dockerfile" && -f "$ROOT/frontend/Dockerfile" ]] || fail 'Not a complete WealthSail source checkout'
command -v python3 >/dev/null || fail 'python3 is required on NAS'
command -v sudo >/dev/null || fail 'sudo is required on NAS'
command -v sha256sum >/dev/null || fail 'sha256sum is required on NAS'
read_admin_secret check

log '[1/7] Validate exact WealthSail Compose project and storage'
# Existing build script has the authoritative production Compose/storage guards.
bash "$BUILD_SCRIPT" --check

# Verify expected PostgreSQL target WITHOUT printing its DATABASE_URL/password.
if [[ "$MODE" == resume ]]; then
  verify_configured_database
  verify_stopped_writers
  DOWNTIME_STARTED=1
else
  sudo "$COMPOSE" ws exec -T api python -c '
import os
from urllib.parse import urlsplit
v=urlsplit(os.environ.get("DATABASE_URL", ""))
host=(v.hostname or "") + (":"+str(v.port) if v.port else "")
target=host+"/"+v.path.strip("/")
assert target == "postgres:5432/wealthsail", "Production API DB target mismatch"
print("OK: API target is postgres:5432/wealthsail (credentials redacted)")
'
fi

# Refuse to run on an unready or unexpected shared PostgreSQL database.
sudo docker inspect -f '{{.State.Running}}' "$EXPECTED_PG_CONTAINER" | grep -qx true \
  || fail "Shared PostgreSQL container $EXPECTED_PG_CONTAINER is not running"
PG_DB_CHECK="$(sudo docker exec "$EXPECTED_PG_CONTAINER" psql -X -q -t -A \
  -U "$EXPECTED_PG_USER" -d "$EXPECTED_PG_DB" \
  -c 'SELECT current_database()' | tr -d '\r\n')"
[[ "$PG_DB_CHECK" == "$EXPECTED_PG_DB" ]] || fail 'PostgreSQL database/admin connection check failed'
printf 'OK: shared PostgreSQL database %s responds to admin locally\n' "$EXPECTED_PG_DB"

log '[SQL] Compare local SQL migrations with the live PostgreSQL ledger (read-only)'
if ! LOCAL_PLAN="$(show_migration_plan)"; then
  printf '%s\n' "$LOCAL_PLAN" >&2
  fail 'Read-only migration comparison failed'
fi
printf '%s\n' "$LOCAL_PLAN"
LOCAL_PENDING_COUNT="$(printf '%s\n' "$LOCAL_PLAN" | sed -n 's/^PENDING_COUNT=//p' | tail -1)"
[[ "$LOCAL_PENDING_COUNT" =~ ^[0-9]+$ ]] || fail 'Failed to determine local migration count'

if [[ "$MODE" == check ]]; then
  echo 'CHECK COMPLETE: migration plan displayed; no image built, SQL migration executed, backup created, or service stopped.'
  exit 0
fi

if [[ "$MODE" == resume ]]; then
  ((LOCAL_PENDING_COUNT == 0)) \
    || fail 'Recovery requires all SQL already applied; pending migrations found'
else
  [[ -t 0 && -t 1 ]] || fail '--apply requires an interactive SSH terminal'
  [[ -r /dev/tty ]] || fail 'No SSH TTY found; run this command interactively'
  for service in api worker codex-runner frontend; do
    cid="$(sudo "$COMPOSE" ws ps -q "$service")"
    [[ -n "$cid" ]] || fail "Service $service must be running before upgrade"
    [[ "$(sudo docker inspect -f '{{.State.Running}}' "$cid")" == true ]] \
      || fail "Service $service is not running"
  done
fi

log '[2/7] Build new backend/runner/frontend images without starting services'
bash "$BUILD_SCRIPT" --build-only

# The db-init Compose service is one-shot and on the existing postgres network.
# Python overrides db-init entrypoint so the initial bootstrap NEVER runs.
# The password is read from stdin in the one-off container; not in command args.
MIGRATION_PYTHON=$(cat <<'PY'
import os
import runpy
import sys
from urllib.parse import quote, urlsplit, urlunsplit

url = urlsplit(os.environ["DATABASE_URL"])
expected = "postgres:5432/wealthsail"
host = (url.hostname or "") + (":" + str(url.port) if url.port else "")
if host + "/" + url.path.strip("/") != expected:
    raise SystemExit("Migration database target mismatch")
password = sys.stdin.readline().rstrip("\r\n")
if not password:
    raise SystemExit("Missing migration admin password on stdin")
admin = os.environ.get("POSTGRES_ADMIN_USER", "admin")
if admin != "admin":
    raise SystemExit("Migration administrator mismatch")
authority = url.netloc.rsplit("@", 1)[-1]
admin_authority = quote(admin, safe="") + ":" + quote(password, safe="") + "@" + authority
os.environ["MIGRATION_DATABASE_URL"] = urlunsplit(url._replace(netloc=admin_authority))
sys.argv = ["scripts/migrate.py", "--environment", "production", "--confirm-target", expected, *sys.argv[1:]]
runpy.run_path("scripts/migrate.py", run_name="__main__")
PY
)

run_migrations() {
  # `-T` disables the db-init service tty so stdin can carry the secret line.
  printf '%s\n' "$PG_ADMIN_PASSWORD" \
    | sudo "$COMPOSE" ws run --rm --no-deps --pull never -T \
        --entrypoint python db-init -c "$MIGRATION_PYTHON" "$@"
}

# Load only after a successful image build, as late as possible.
# The script runs as root (`sudo bash ... --apply`); password is kept in a
# non-exported shell variable and passed to the migration process by stdin.
PG_ADMIN_PASSWORD="$(read_admin_secret load)" || fail 'Unable to read protected admin credential file'
[[ -n "$PG_ADMIN_PASSWORD" ]] || fail 'Administrator password is empty'

log '[3/7] Preview forward-only migrations (no pending SQL applied)'
PLAN_LOG="$(mktemp)"
run_migrations --dry-run 2>&1 | tee "$PLAN_LOG"
grep -q '^TARGET environment=production database=postgres:5432/wealthsail dry_run=True' "$PLAN_LOG" \
  || fail 'Migration dry-run did not verify the expected production target'
IMAGE_PENDING_COUNT="$(grep -c '^PLAN ' "$PLAN_LOG" || true)"
[[ "$IMAGE_PENDING_COUNT" == "$LOCAL_PENDING_COUNT" ]] \
  || fail "Built-image migration plan differs from local source (source=$LOCAL_PENDING_COUNT image=$IMAGE_PENDING_COUNT)"
LOCAL_PLAN_FILES="$(printf '%s\n' "$LOCAL_PLAN" | sed -n 's/^PLAN \([^ ]*\).*/\1/p' | sort)"
IMAGE_PLAN_FILES="$(sed -n 's/^PLAN \([^ ]*\).*/\1/p' "$PLAN_LOG" | sort)"
[[ "$LOCAL_PLAN_FILES" == "$IMAGE_PLAN_FILES" ]] \
  || fail 'Built-image planned migration filenames differ from source SQL files'
if [[ "$MODE" == resume ]]; then
  verify_skipped_migrations "$PLAN_LOG"
  verify_stopped_writers
  log '[4/7] Recovery: preserve existing backup; no migration SQL will execute'
  log '[5/7] Run complete readback in a read-only transaction under the migration lock'
  MIGRATION_ATTEMPTED=1
  APPLY_LOG="$(mktemp)"
  run_migrations --readback-only 2>&1 | tee "$APPLY_LOG"
  grep -qx 'TARGET environment=production database=postgres:5432/wealthsail dry_run=False readback_only=True' "$APPLY_LOG" \
    || fail 'Recovery did not verify the expected production target'
  verify_skipped_migrations "$APPLY_LOG"
  grep -q '^READBACK OK:' "$APPLY_LOG" || fail 'Recovery ended without READBACK OK'
  verify_stopped_writers
  log '[6/7] Recheck migration ledger before deploying'
  if ! POST_PLAN="$(show_migration_plan)"; then
    printf '%s\n' "$POST_PLAN" >&2
    fail 'Recovery ledger checksum validation failed'
  fi
  printf '%s\n' "$POST_PLAN"
  grep -qx 'PENDING_COUNT=0' <<< "$POST_PLAN" \
    || fail 'Recovery found unapplied SQL before deployment'
  MIGRATION_SUCCESS=1
  PG_ADMIN_PASSWORD=''
  unset PG_ADMIN_PASSWORD
  verify_stopped_writers
  log '[7/7] Readback passed: deploy verified images and check services'
  bash "$BUILD_SCRIPT" --deploy-only
  sudo "$COMPOSE" ws ps
  printf '\nSUCCESS: recovery readback passed, deployment checked; original backup preserved.\n'
  printf 'NEXT: Verify HTTPS web login, research queue operations and worker logs.\n'
  exit 0
fi
if ((IMAGE_PENDING_COUNT == 0)); then
  log '[4/7] No pending SQL: leave database online and deploy new images only'
  PG_ADMIN_PASSWORD=''
  unset PG_ADMIN_PASSWORD
  bash "$BUILD_SCRIPT" --deploy-only
  printf '\nSUCCESS: no pending SQL; code-only deployment complete (no database downtime).\n'
  exit 0
fi

printf '\nIMPORTANT: every PLAN version above will be applied.\n'
printf 'API/Worker downtime begins after confirmation. No automatic SQL rollback.\n'
printf 'Type APPLY wealthsail to continue: ' > /dev/tty
IFS= read -r confirmation < /dev/tty || fail 'Unable to read approval'
[[ "$confirmation" == 'APPLY wealthsail' ]] || fail 'Confirmation did not match; upgrade aborted with services running'

log '[4/7] Stop ONLY API and Worker to prevent new writes during migration'
DOWNTIME_STARTED=1
sudo "$COMPOSE" ws stop worker api
for service in api worker; do
  cid="$(sudo "$COMPOSE" ws ps -a -q "$service")"
  if [[ -n "$cid" ]]; then
    running="$(sudo docker inspect -f '{{.State.Running}}' "$cid")"
    [[ "$running" == false ]] || fail "Service $service is still running"
  fi
done

log '[5/7] Create a PRIVATE, verified pre-migration PostgreSQL dump'
# Under existing persisted business backup root, not inside git or Docker volumes.
[[ ! -L "$BACKUP_DIR" ]] || fail 'Backup directory is a symbolic link'
sudo install -d -m 0700 -- "$BACKUP_DIR"
sudo chmod 0700 -- "$BACKUP_DIR"
BACKUP_STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_TMP="$(sudo mktemp "$BACKUP_DIR/.wealthsail-$BACKUP_STAMP-XXXXXX.partial")"
if ! sudo sh -c 'exec docker exec postgres pg_dump -U admin -Fc -d wealthsail > "$1"' _ "$BACKUP_TMP"; then
  sudo rm -f -- "$BACKUP_TMP"
  fail 'pg_dump failed; API/Worker stay stopped and no migrations have been applied'
fi
[[ "$(sudo stat -c '%s' -- "$BACKUP_TMP")" -gt 1024 ]] || fail 'Backup is unexpectedly small'
# pg_restore must parse the compressed custom-format archive (does NOT restore).
if ! sudo sh -c 'exec docker exec -i postgres pg_restore --list < "$1" > /dev/null' _ "$BACKUP_TMP"; then
  fail "Backup could not be parsed by pg_restore; inspect $BACKUP_TMP"
fi
BACKUP_FILE="$BACKUP_DIR/wealthsail-$BACKUP_STAMP.dump"
sudo mv -n -- "$BACKUP_TMP" "$BACKUP_FILE"
sudo chmod 0600 -- "$BACKUP_FILE"
printf 'Verified backup: %s\n' "$BACKUP_FILE"
sudo sha256sum -- "$BACKUP_FILE"

log '[6/7] Apply migrations with the administrator, then require READBACK OK'
MIGRATION_ATTEMPTED=1
APPLY_LOG="$(mktemp)"
run_migrations 2>&1 | tee "$APPLY_LOG"
grep -q '^READBACK OK:' "$APPLY_LOG" || fail 'Migration ended without READBACK OK'
# Cross-check the real ledger and current SQL files after the migration.
if ! POST_PLAN="$(show_migration_plan)"; then
  printf '%s\n' "$POST_PLAN" >&2
  fail 'Post-migration ledger checksum validation failed'
fi
printf '%s\n' "$POST_PLAN"
grep -qx 'PENDING_COUNT=0' <<< "$POST_PLAN" \
  || fail 'Some SQL migrations remain unapplied after migration'
MIGRATION_SUCCESS=1
# The migration and future processes no longer need the administrator password.
PG_ADMIN_PASSWORD=''
unset PG_ADMIN_PASSWORD

log '[7/7] Deploy images and verify the existing project'
bash "$BUILD_SCRIPT" --deploy-only
sudo "$COMPOSE" ws ps
printf '\nSUCCESS: backup verified, migration readback passed, deployment checked.\n'
printf 'Database backup: %s\n' "$BACKUP_FILE"
printf 'NEXT: Verify HTTPS web login, AI usage page, Codex chat, and worker logs.\n'
