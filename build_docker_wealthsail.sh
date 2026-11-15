#!/usr/bin/env bash
# WealthSail NAS production build/deploy. Store this file in the WealthSail repo root.
set -Eeuo pipefail
umask 077

NAS_TOOLS="${NAS_TOOLS:-/data_n002/data/udata/real/15221151691/docker/cloudflared/tools}"
COMPOSE="$NAS_TOOLS/nas-compose"
DEPLOY="$NAS_TOOLS/deploy.json"
WEALTH_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
EXPECTED_AVATARS='/data_n002/data/udata/real/15221151691/docker/wealth-sail/avatars'
EXPECTED_CODEX_AUTH='/data_n002/data/udata/real/15221151691/docker/codex/wealthsail/auth'

MODE=all
MIGRATION_TARGET=""
usage() {
  cat <<'HELP'
Usage: bash build_docker_wealthsail.sh [--check | --build-only | --deploy-only] [--migrate HOST:PORT/DB]

  (no args)      Validate -> build API/backend + frontend -> deploy -> verify
  --check        Read-only Compose, volumes and container/login checks
  --build-only   Validate, build and verify image revision; do not deploy
  --deploy-only  Verify and deploy already-built images; never build implicitly
  --migrate      Dry-run, migrate and read back the confirmed production target
                 before switching services. Export MIGRATION_DATABASE_URL first.

Uses a dedicated nas-compose wrapper. NEVER runs db-init, down -v or prune.
Override wrapper directory with NAS_TOOLS=/actual/tools if needed.
HELP
}
while (($#)); do
  case "$1" in
    --check) [[ "$MODE" == all ]] || { echo 'Only one mode allowed' >&2; exit 2; }; MODE=check ;;
    --build-only) [[ "$MODE" == all ]] || { echo 'Only one mode allowed' >&2; exit 2; }; MODE=build ;;
    --deploy-only) [[ "$MODE" == all ]] || { echo 'Only one mode allowed' >&2; exit 2; }; MODE=deploy ;;
    --migrate)
      [[ -z "$MIGRATION_TARGET" && -n "${2:-}" && "$2" != --* ]] || { echo '--migrate requires an exact target' >&2; exit 2; }
      MIGRATION_TARGET="$2"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
  esac
  shift
done
if [[ -n "$MIGRATION_TARGET" ]]; then
  [[ "$MODE" != check && "$MODE" != build && -n "${MIGRATION_DATABASE_URL:-}" ]] || {
    echo '--migrate requires an update mode and an exported MIGRATION_DATABASE_URL' >&2
    exit 2
  }
fi
[[ -x "$COMPOSE" && -f "$DEPLOY" ]] || {
  echo "Missing nas-compose/deploy.json under $NAS_TOOLS" >&2; exit 1;
}

printf '\n[1/5] Validate merged WealthSail Compose and persistent volumes\n'
sudo "$COMPOSE" ws config --quiet
sudo "$COMPOSE" ws config --format json | python3 -c '
import json,os,sys
c=json.load(sys.stdin)
d=json.load(open(sys.argv[1],encoding="utf-8"))
root,avatars,auth=sys.argv[2:]
def real(p): return os.path.realpath(p)
def check(ok,msg):
 if not ok: raise SystemExit("FAILED safety check: "+msg)
 print("OK:",msg)
check(c.get("name")=="wealthsail", "project name wealthsail")
check(real(d["wealth"])==real(root), "script and wrapper use the same WealthSail source")
services=c["services"]
for name in ("api","worker","frontend","codex-runner"):
 check(name in services, "service exists: "+name)
for name in ("api","worker","codex-runner"):
 check(services[name].get("image")=="wealthsail-backend:latest", "shared backend image: "+name)
check(services["frontend"].get("image")=="wealthsail-frontend:latest", "frontend image")
check(not any(service.get("ports") for service in services.values()), "NO service publishes NAS host ports")
check("wealth_edge" in services["frontend"].get("networks",{}), "frontend connected to Cloudflare edge")
check("postgres_shared" in services["api"].get("networks",{}), "API connected to existing PostgreSQL network")
check(real(services["api"]["build"]["context"])==real(root), "backend build context")
check(real(services["frontend"]["build"]["context"])==real(os.path.join(root,"frontend")), "frontend build context")
volumes=c["volumes"]
check(real(volumes["avatar_data"]["driver_opts"]["device"])==real(avatars), "same avatar volume path")
check(real(volumes["codex_auth"]["driver_opts"]["device"])==real(auth), "same Codex auth volume path")
' "$DEPLOY" "$WEALTH_ROOT" "$EXPECTED_AVATARS" "$EXPECTED_CODEX_AUTH"

printf '\n[2/5] Check for unexpected Compose projects\n'
mapfile -t WS_IDS < <(sudo docker ps -aq --filter 'label=com.docker.compose.project=wealthsail')
if ((${#WS_IDS[@]})); then
  sudo docker inspect "${WS_IDS[@]}" | python3 -c '
import json,sys
for c in json.load(sys.stdin):
  label=c["Config"].get("Labels",{}).get("com.docker.compose.project")
  if label!="wealthsail": raise SystemExit("Unexpected Compose project")
  print("OK:",c["Name"].lstrip("/"))
'
else
  echo 'No current WealthSail container under this project'
fi

if [[ "$MODE" == check ]]; then
  sudo "$COMPOSE" ws ps
  if [[ -n "$(sudo "$COMPOSE" ws ps -q codex-runner)" ]]; then
    sudo "$COMPOSE" ws exec -T codex-runner codex login status
  fi
  echo 'Read-only checks complete.'
  exit 0
fi

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

EXPECTED_COMMIT="$(git -C "$WEALTH_ROOT" rev-parse --verify HEAD)"
[[ "$EXPECTED_COMMIT" =~ ^([0-9a-f]{40}|[0-9a-f]{64})$ ]] \
  || fail 'Cannot determine a complete Git commit for this operation'
declare -A VERIFIED_IMAGE_IDS=()

verify_source_commit() {
  local current
  current="$(git -C "$WEALTH_ROOT" rev-parse --verify HEAD)" \
    || fail 'Cannot read Git HEAD while verifying deployment'
  [[ "$current" == "$EXPECTED_COMMIT" ]] \
    || fail "Git HEAD changed during this operation (expected $EXPECTED_COMMIT, got $current)"
  python3 "$WEALTH_ROOT/scripts/check_build_sources.py" --root "$WEALTH_ROOT" \
    || fail 'Build inputs do not match committed source; see the source check above'
}

verify_images() {
  local kind metadata image_id revision
  for kind in backend frontend; do
    metadata="$(sudo docker image inspect "wealthsail-$kind:latest" \
      --format '{{.Id}} {{index .Config.Labels "org.opencontainers.image.revision"}}')" \
      || fail "Cannot inspect the $kind image; build it before deploying"
    [[ "$metadata" != *$'\n'* ]] || fail "$kind image metadata contains multiple records"
    read -r image_id revision <<< "$metadata"
    [[ "$image_id" =~ ^sha256:[0-9a-f]{64}$ ]] \
      || fail "$kind image ID is missing or invalid"
    [[ "$revision" == "$EXPECTED_COMMIT" ]] \
      || fail "$kind image Git commit mismatch (expected $EXPECTED_COMMIT, got ${revision:-<missing>})"
    [[ -z "${VERIFIED_IMAGE_IDS[$kind]:-}" || "$image_id" == "${VERIFIED_IMAGE_IDS[$kind]}" ]] \
      || fail "$kind image tag changed during this operation"
    VERIFIED_IMAGE_IDS["$kind"]="$image_id"
    echo "OK: $kind image matches Git commit $EXPECTED_COMMIT"
  done
}

verify_running_services() {
  local service kind container_ids container_id metadata image_id running project actual_service oneoff revision runtime_count current_ids
  local -A verified_ids=()
  for service in api worker codex-runner frontend; do
    kind=backend
    [[ "$service" != frontend ]] || kind=frontend
    container_ids="$(sudo "$COMPOSE" ws ps --all -q "$service" | LC_ALL=C sort)" \
      || fail "Cannot list containers for $service"
    verified_ids["$service"]="$container_ids"
    runtime_count=0
    while IFS= read -r container_id; do
      [[ -n "$container_id" ]] || continue
      metadata="$(sudo docker container inspect "$container_id" \
        --format '{{.Image}} {{.State.Running}} {{index .Config.Labels "com.docker.compose.project"}} {{index .Config.Labels "com.docker.compose.service"}} {{index .Config.Labels "com.docker.compose.oneoff"}}')" \
        || fail "Cannot inspect container for $service"
      read -r image_id running project actual_service oneoff <<< "$metadata"
      [[ "$project" == wealthsail && "$actual_service" == "$service" ]] \
        || fail "Unexpected container identity for $service"
      [[ "${oneoff,,}" != true ]] || continue
      [[ "${oneoff,,}" == false && "$running" == true ]] \
        || fail "Runtime container for $service is not confirmed running"
      runtime_count=$((runtime_count + 1))
      [[ "$runtime_count" == 1 ]] || fail "More than one runtime container found for $service"
      [[ "$image_id" == "${VERIFIED_IMAGE_IDS[$kind]}" ]] \
        || fail "Running $service uses a different $kind image"
      revision="$(sudo docker image inspect "$image_id" \
        --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')" \
        || fail "Cannot inspect the running image for $service"
      [[ "$revision" == "$EXPECTED_COMMIT" ]] \
        || fail "Running $service image Git commit mismatch"
    done <<< "$container_ids"
    [[ "$runtime_count" == 1 ]] || fail "Exactly one runtime container is required for $service"
    echo "OK: running $service matches Git commit $EXPECTED_COMMIT"
  done
  # A healthy old instance must not certify a concurrent replacement.
  for service in api worker codex-runner frontend; do
    current_ids="$(sudo "$COMPOSE" ws ps --all -q "$service" | LC_ALL=C sort)" \
      || fail "Cannot recheck containers for $service"
    [[ "$current_ids" == "${verified_ids[$service]}" ]] \
      || fail "Container set changed while verifying $service"
  done
}

verify_release_endpoints() {
  local payload field service
  local api_probe="import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/healthz',timeout=5).read().decode())"
  for service in frontend api; do
    if [[ "$service" == frontend ]]; then
      payload="$(sudo "$COMPOSE" ws exec -T frontend wget -qO- http://127.0.0.1/build-info.json)"
      field=commit
    else
      payload="$(sudo "$COMPOSE" ws exec -T api python -c "$api_probe")"
      field=gitCommit
    fi
    printf '%s' "$payload" | python3 -c '
import json,sys
metadata=json.load(sys.stdin)
field,expected,service=sys.argv[1:]
if metadata.get(field) != expected:
    raise SystemExit(f"ERROR: {service} response Git revision does not match {expected}")
if service == "frontend" and not metadata.get("describe"):
    raise SystemExit("ERROR: frontend build is missing git describe")
if service == "api" and metadata.get("status") != "ok":
    raise SystemExit("ERROR: backend health response is not ready")
print(f"OK: {service} response matches Git commit {expected}")
' "$field" "$EXPECTED_COMMIT" "$service"
  done
}

if [[ "$MODE" != deploy ]]; then
  verify_source_commit
  BUILD_DESCRIBE="$(git -C "$WEALTH_ROOT" describe --tags --always --long --dirty)"
  printf '\n[3/5] Build backend (shared API/Worker/Runner) and frontend\n'
  sudo env BUILD_COMMIT="$EXPECTED_COMMIT" BUILD_DESCRIBE="$BUILD_DESCRIBE" "$COMPOSE" ws build api worker codex-runner frontend
fi
verify_source_commit
verify_images
if [[ "$MODE" == build ]]; then
  echo '[build-only] Images ready; running service unchanged.'
  exit 0
fi

if [[ -n "$MIGRATION_TARGET" ]]; then
  migrate=(ws run --rm --no-deps -T -e MIGRATION_DATABASE_URL api python scripts/migrate.py
    --environment production --confirm-target "$MIGRATION_TARGET")
  sudo --preserve-env=MIGRATION_DATABASE_URL "$COMPOSE" "${migrate[@]}" --dry-run
  sudo "$COMPOSE" ws stop worker api
  sudo --preserve-env=MIGRATION_DATABASE_URL "$COMPOSE" "${migrate[@]}"
  sudo --preserve-env=MIGRATION_DATABASE_URL "$COMPOSE" "${migrate[@]}" --readback-only
  verify_source_commit
  verify_images
fi

printf '\n[4/5] Deploy only WealthSail runtime services\n'
sudo "$COMPOSE" ws up -d --pull never --no-build --wait --wait-timeout 180 codex-runner api worker frontend

printf '\n[5/5] Verify health and Codex authentication\n'
sudo "$COMPOSE" ws ps
sudo "$COMPOSE" ws exec -T frontend nginx -t
sudo "$COMPOSE" ws exec -T codex-runner codex --version
sudo "$COMPOSE" ws exec -T codex-runner codex login status
verify_source_commit
verify_images
verify_running_services
verify_release_endpoints
verify_source_commit
verify_images
echo 'WealthSail deployment checks complete. Check Web login and Codex chat in browser.'
