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
usage() {
  cat <<'HELP'
Usage: bash build_docker_wealthsail.sh [--check | --build-only | --deploy-only]

  (no args)      Validate -> build API/backend + frontend -> deploy -> verify
  --check        Read-only Compose, volumes and container/login checks
  --build-only   Validate and build images; do not deploy
  --deploy-only  Validate and deploy already-built images

Uses a dedicated nas-compose wrapper. NEVER runs db-init, down -v or prune.
Override wrapper directory with NAS_TOOLS=/actual/tools if needed.
HELP
}
for arg in "$@"; do
  case "$arg" in
    --check) [[ "$MODE" == all ]] || { echo 'Only one mode allowed' >&2; exit 2; }; MODE=check ;;
    --build-only) [[ "$MODE" == all ]] || { echo 'Only one mode allowed' >&2; exit 2; }; MODE=build ;;
    --deploy-only) [[ "$MODE" == all ]] || { echo 'Only one mode allowed' >&2; exit 2; }; MODE=deploy ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $arg" >&2; usage; exit 2 ;;
  esac
done
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

if [[ "$MODE" != deploy ]]; then
  printf '\n[3/5] Build backend (shared API/Worker/Runner) and frontend\n'
  sudo "$COMPOSE" ws build api frontend
fi
if [[ "$MODE" == build ]]; then
  echo '[build-only] Images ready; running service unchanged.'
  exit 0
fi

printf '\n[4/5] Deploy only WealthSail runtime services\n'
sudo "$COMPOSE" ws up -d --pull never --wait --wait-timeout 180 codex-runner api worker frontend

printf '\n[5/5] Verify health and Codex authentication\n'
sudo "$COMPOSE" ws ps
sudo "$COMPOSE" ws exec -T frontend nginx -t
sudo "$COMPOSE" ws exec -T codex-runner codex --version
sudo "$COMPOSE" ws exec -T codex-runner codex login status
echo 'WealthSail deployment checks complete. Check Web login and Codex chat in browser.'
