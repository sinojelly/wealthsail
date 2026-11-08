"""Exercise interrupted-upgrade recovery with inert Docker/NAS boundaries.

The real Bash script and its Python ledger comparison run from a temporary
checkout. Only privileged commands, builds, and the database process are fakes.
No production Docker daemon, NAS configuration, or credential file is touched.
"""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
BASH = shutil.which("bash") if os.name != "nt" else r"C:\Program Files\Git\bin\bash.exe"
pytestmark = pytest.mark.skipif(
    not BASH or not Path(BASH).is_file(), reason="local Bash fixture required"
)
MIGRATIONS = (
    "104_opportunity_fund_cache.sql",
    "105_research_queue_control.sql",
    "106_research_local_exit_evidence.sql",
)
SECRET = "fixture-only-$#=&-admin-secret"


DISPATCHER = r'''
import hashlib, json, os, pathlib, subprocess, sys
sys.stdout.reconfigure(newline="\n")
state_path = pathlib.Path(os.environ["FIXTURE_STATE"])
state = json.loads(state_path.read_text())
scenario = state["scenario"]
kind, args = sys.argv[1], sys.argv[2:]
secret = "fixture-only-$#=&-admin-secret"
root = pathlib.Path(os.environ["FIXTURE_ROOT"])
files = sorted((root / "migrations").glob("*.sql"))
state["calls"].append([kind, *args])
def save(): state_path.write_text(json.dumps(state))
def finish(value="", code=0):
 save()
 if value != "": print(value)
 raise SystemExit(code)
if kind == "secret":
 finish(secret if args[-1] == "load" else "OK: protected root-only DB admin credential file (secret redacted)")
if kind == "build":
 mode = args[-1]
 if mode == "--check": finish(code=1 if scenario.get("preflight_failure") else 0)
 if mode == "--build-only":
  state["built"] = True
  finish(code=1 if scenario.get("build_failure") else 0)
 if mode == "--deploy-only":
  state["deployed"] = True
  state["writers_running"] = True
  finish(code=1 if scenario.get("deploy_failure") else 0)
 finish(code=96)
if kind == "compose":
 if not args or args.pop(0) != "ws": finish(code=96)
 tools_profile = args[:2] == ["--profile", "tools"]
 if tools_profile: del args[:2]
 command = args.pop(0)
 if command == "config":
  database = "other" if scenario.get("wrong_target") else "wealthsail"
  target = "postgresql://runtime:fixture@postgres:5432/" + database
  services = {name: {"environment": {"DATABASE_URL": target, "WORKER_DATABASE_URL": target},
                           "image": "wealthsail-backend:latest"}
              for name in ("api", "worker", "db-init")}
  services["worker"]["environment"]["DATABASE_URL"] = ""
  if scenario.get("wrong_worker_target"):
   services["worker"]["environment"]["WORKER_DATABASE_URL"] = "postgresql://runtime:fixture@other:5432/wealthsail"
  if scenario.get("wrong_db_init_image"): services["db-init"]["image"] = "foreign-backend:latest"
  if scenario.get("wrong_admin"): services["db-init"]["environment"]["POSTGRES_ADMIN_USER"] = "foreign-admin"
  # Compose excludes inactive profile services from its resolved model.
  if not tools_profile or scenario.get("missing_db_init"): services.pop("db-init")
  if scenario.get("wrong_db_init_target"):
   services["db-init"]["environment"]["DATABASE_URL"] = "postgresql://runtime:fixture@other:5432/wealthsail"
  finish(json.dumps({"name": "wealthsail", "services": services}), code=1 if scenario.get("config_failure") else 0)
 if command == "ps":
  if "-q" in args:
   finish("fixture-" + args[-1])
  finish()
 if command == "stop":
  if set(args) != {"worker", "api"}: finish(code=96)
  state["writers_running"] = False
  finish()
 if command == "run":
  if not all(item in args for item in ("--rm", "--no-deps", "--pull", "never", "-T", "--entrypoint", "python", "db-init", "-c")):
   finish(code=96)
  received = sys.stdin.read()
  state["secret_on_stdin"] = received == secret + "\n"
  state["secret_in_environment"] = any(secret in value for value in os.environ.values())
  if not state["secret_on_stdin"] or state["secret_in_environment"]: finish(code=95)
  dry = "--dry-run" in args
  if not dry and "--readback-only" not in args: finish(code=94)
  if state["writers_running"]: finish("writers unexpectedly running", code=93)
  phase = "dry_run" if dry else "readback"
  state[phase] = True
  target = "other:5432/wealthsail" if scenario.get(phase + "_wrong_target") else "postgres:5432/wealthsail"
  print("TARGET environment=production database=" + target + " dry_run=" + str(dry) + ("" if dry else " readback_only=True"))
  for index, path in enumerate(files):
   if scenario.get(phase + "_missing_skip") and index == 0: continue
   if dry and scenario.get("image_pending") and index == 0:
    print("PLAN " + path.name)
   elif not dry and scenario.get("readback_reconciled_skip") and index == 0:
    print("SKIP " + path.name + " (reconciled)")
   else: print("SKIP " + path.name + " (already applied)")
  if not dry:
   if scenario.get("readback_duplicate_skip"): print("SKIP " + files[0].name + " (already applied)")
   if scenario.get("readback_unknown_skip"): print("SKIP 999_unknown.sql (already applied)")
   if scenario.get("readback_applied"): print("APPLIED 106_research_local_exit_evidence.sql")
   if scenario.get("readback_plan"): print("PLAN 106_research_local_exit_evidence.sql")
   if scenario.get("readback_reconciled"): print("RECONCILE 106_research_local_exit_evidence.sql")
   if not scenario.get("missing_readback_ok"): print("READBACK OK: fixture permissions checked")
  if dry and scenario.get("writer_starts_after_dry_run"): state["writers_running"] = True
  finish(code=1 if scenario.get(phase + "_failure") else 0)
 # Recovery must never need an API container exec or start services itself.
 finish(code=96)
if kind == "docker":
 if args and args[0] == "inspect":
  if args[-1] == "postgres": finish("true")
  if args[-1] in {"fixture-api", "fixture-worker"}:
   finish("true" if state["writers_running"] else "false")
  finish(code=96)
 if args[:2] == ["exec", "postgres"] and "current_database()" in args[-1]:
  finish("other" if scenario.get("wrong_database") else "wealthsail")
 finish(code=96)
if kind == "plan-python":
 # Keep the production Python checksum/parser code; replace only PostgreSQL I/O.
 source = sys.stdin.read()
 def query(command, **kwargs):
  if not command[:4] == ["sudo", "docker", "exec", "postgres"]:
   raise AssertionError("Unexpected subprocess in ledger check")
  state["ledger_checks"] = state.get("ledger_checks", 0) + 1
  if scenario.get("ledger_failure"):
   save()
   raise subprocess.CalledProcessError(1, command)
  rows = []
  for index, path in enumerate(files):
   if scenario.get("pending") and index == 0: continue
   digest = hashlib.sha256(path.read_text(encoding="utf-8").encode()).hexdigest()
   drift = scenario.get("checksum_drift") or (scenario.get("post_checksum_drift") and state.get("readback"))
   if drift and index == 0: digest = "0" * 64
   name = "104_renamed.sql" if scenario.get("filename_drift") and index == 0 else path.name
   rows.append(str(int(path.name[:3])) + "\t" + name + "\t" + digest)
  if scenario.get("unknown_ledger_version"): rows.append("999\t999_unknown.sql\t" + "0" * 64)
  save()
  return subprocess.CompletedProcess(command, 0, "\n".join(rows) + "\n", "")
 subprocess.run = query
 sys.argv = args
 exec(compile(source, "<production migration plan>", "exec"), {"__name__": "__main__"})
 raise SystemExit(0)
finish(code=96)
'''


SHELL_ENV = r'''
fixture_dispatch() { MSYS_NO_PATHCONV=1 "$FIXTURE_PY" "$FIXTURE_DISPATCHER" "$@"; }
sudo() {
 case "${1:-}" in
  python3) shift; fixture_dispatch secret "$@" ;;
  docker) shift; fixture_dispatch docker "$@" ;;
  "$NAS_TOOLS/nas-compose") shift; fixture_dispatch compose "$@" ;;
  *) printf '%s\n' 'Fixture rejected unexpected privileged command' >&2; return 97 ;;
 esac
}
bash() {
 if [[ "${1:-}" == */build_docker_wealthsail.sh ]]; then
  shift; fixture_dispatch build "$@"
 else command bash "$@"; fi
}
python3() {
 local arg
 local -a native=()
 for arg in "$@"; do
  if [[ "$FIXTURE_WINDOWS" == 1 && "$arg" == /* ]]; then
   native+=("$(cygpath -w "$arg")")
  else native+=("$arg"); fi
 done
 if [[ $# -eq 5 && "${1:-}" == - && "${2:-}" == */migrations ]]; then
  fixture_dispatch plan-python "${native[@]}"
 else MSYS_NO_PATHCONV=1 "$FIXTURE_PY" "${native[@]}"; fi
}
'''


def run_resume(tmp_path, scenario=None):
    checkout, tools = tmp_path / "checkout", tmp_path / "tools"
    checkout.mkdir()
    tools.mkdir()
    for directory in ("scripts", "migrations", "backend", "frontend"):
        (checkout / directory).mkdir()
    for filename in MIGRATIONS:
        shutil.copyfile(ROOT / "migrations" / filename, checkout / "migrations" / filename)
    for filename in ("scripts/migrate.py", "backend/Dockerfile", "frontend/Dockerfile"):
        (checkout / filename).touch()
    script = checkout / "upgrade_docker_wealthsail_db.sh"
    script.write_bytes((ROOT / script.name).read_bytes())
    (checkout / "build_docker_wealthsail.sh").touch()
    wrapper = tools / "nas-compose"
    wrapper.write_text("#!/usr/bin/env bash\nexit 97\n", encoding="utf-8")
    wrapper.chmod(0o700)
    (tools / "deploy.json").write_text("{}", encoding="utf-8")
    state_file = tmp_path / "state.json"
    state_file.write_text(
        json.dumps({"scenario": scenario or {}, "calls": [], "writers_running": bool((scenario or {}).get("writer_running"))}),
        encoding="utf-8",
    )
    dispatcher, shell_env = tmp_path / "dispatcher.py", tmp_path / "shell-env.sh"
    dispatcher.write_text(DISPATCHER, encoding="utf-8")
    shell_env.write_text(SHELL_ENV, encoding="utf-8")
    env = {
        key: value
        for key, value in os.environ.items()
        if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "COMSPEC"}
    }
    env.update(
        {
            "BASH_ENV": shell_env.as_posix(),
            "NAS_TOOLS": tools.as_posix(),
            "FIXTURE_STATE": state_file.as_posix(),
            "FIXTURE_ROOT": checkout.as_posix(),
            "FIXTURE_PY": Path(sys.executable).as_posix(),
            "FIXTURE_DISPATCHER": dispatcher.as_posix(),
            "FIXTURE_WINDOWS": "1" if os.name == "nt" else "0",
            "MSYS_NO_PATHCONV": "1",
            "MSYS2_ENV_CONV_EXCL": "*",
        }
    )
    if (scenario or {}).get("inherited_admin_variable"):
        env["PG_ADMIN_PASSWORD"] = "caller-exported-value"
    result = subprocess.run(
        [BASH, "--noprofile", "--norc", str(script), "--resume-readback"],
        cwd=checkout,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result, json.loads(state_file.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "scenario",
    [{}, {"inherited_admin_variable": True}],
    ids=["stopped-writers", "inherited-password-variable"],
)
def test_resume_checks_stopped_database_before_starting_services(tmp_path, scenario):
    result, state = run_resume(tmp_path, scenario)
    assert result.returncode == 0, result.stdout + result.stderr
    assert state.get("built") and state.get("readback") and state.get("deployed")
    assert state["ledger_checks"] >= 2
    calls = state["calls"]
    build = next(i for i, call in enumerate(calls) if call == ["build", "--build-only"])
    dry_run = next(i for i, call in enumerate(calls) if call[:3] == ["compose", "ws", "run"] and "--dry-run" in call)
    readback = next(i for i, call in enumerate(calls) if call[:3] == ["compose", "ws", "run"] and "--readback-only" in call)
    deploy = calls.index(["build", "--deploy-only"])
    assert build < dry_run < readback < deploy
    load_secret = next(i for i, call in enumerate(calls) if call[0] == "secret" and call[-1] == "load")
    assert build < load_secret < dry_run
    assert state["secret_on_stdin"] and not state["secret_in_environment"]
    assert SECRET not in result.stdout + result.stderr + json.dumps(calls)
    assert not any(call[:3] == ["compose", "ws", "exec"] for call in calls)
    for filename in MIGRATIONS:
        assert "SKIP " + filename + " (already applied)" in result.stdout
    assert "READBACK OK:" in result.stdout


def assert_no_rollback(calls):
    assert not any(
        call[:2] == ["compose", "ws"] and call[2] in {"up", "start", "restart", "down"}
        for call in calls
    )
    assert not any("pg_restore" in call or "--reconcile-drift" in call for call in calls)


@pytest.mark.parametrize(
    "scenario",
    [
        {"wrong_target": True},
        {"wrong_worker_target": True},
        {"wrong_database": True},
        {"config_failure": True},
        {"wrong_db_init_image": True},
        {"wrong_admin": True},
        {"pending": True},
        {"checksum_drift": True},
        {"filename_drift": True},
        {"unknown_ledger_version": True},
        {"ledger_failure": True},
        {"build_failure": True},
        {"image_pending": True},
        {"dry_run_wrong_target": True},
        {"dry_run_missing_skip": True},
        {"dry_run_failure": True},
        {"readback_wrong_target": True},
        {"readback_missing_skip": True},
        {"missing_readback_ok": True},
        {"readback_failure": True},
        {"readback_applied": True},
        {"readback_plan": True},
        {"readback_reconciled": True},
        {"readback_reconciled_skip": True},
        {"readback_duplicate_skip": True},
        {"readback_unknown_skip": True},
        {"post_checksum_drift": True},
        {"writer_starts_after_dry_run": True},
    ],
    ids=lambda scenario: next(iter(scenario)),
)
def test_resume_failure_never_deploys_or_restarts_writers(tmp_path, scenario):
    result, state = run_resume(tmp_path, scenario)
    assert result.returncode != 0, result.stdout + result.stderr
    assert not state.get("deployed")
    assert state["writers_running"] is False
    assert SECRET not in result.stdout + result.stderr + json.dumps(state["calls"])
    assert_no_rollback(state["calls"])
    if set(scenario) & {"pending", "checksum_drift", "filename_drift", "unknown_ledger_version", "ledger_failure"}:
        assert state.get("ledger_checks") == 1 and not state.get("built")
    if set(scenario) & {"image_pending", "dry_run_wrong_target", "dry_run_missing_skip", "dry_run_failure"}:
        assert state.get("dry_run") and not state.get("readback")
    if any(key.startswith("readback_") or key == "missing_readback_ok" for key in scenario):
        assert state.get("readback")
    if scenario.get("build_failure"):
        assert state.get("built") and not state.get("dry_run")
    if scenario.get("post_checksum_drift"):
        assert state.get("readback") and state["ledger_checks"] >= 2
    if scenario.get("writer_starts_after_dry_run"):
        assert state.get("dry_run") and not state.get("readback")


def test_failed_deployment_stops_writers_again_without_rollback(tmp_path):
    result, state = run_resume(tmp_path, {"deploy_failure": True})
    assert result.returncode != 0
    assert state.get("readback") and state.get("deployed")
    assert not state["writers_running"]
    deploy = state["calls"].index(["build", "--deploy-only"])
    assert ["compose", "ws", "stop", "worker", "api"] in state["calls"][deploy + 1 :]
    assert_no_rollback(state["calls"])


def test_resume_refuses_running_writers_instead_of_turning_it_into_upgrade(tmp_path):
    result, state = run_resume(tmp_path, {"writer_running": True})
    assert result.returncode != 0
    assert not state.get("built") and not state.get("readback") and not state.get("deployed")
    assert state["writers_running"]


def test_resume_reports_missing_tool_service_before_build_or_database_changes(tmp_path):
    result, state = run_resume(tmp_path, {"missing_db_init": True})
    assert result.returncode != 0
    assert "Required Compose service missing: db-init" in result.stderr
    assert "Configured database target mismatch" not in result.stderr
    assert not state.get("built") and not state.get("dry_run")
    assert not state.get("readback") and not state.get("deployed")
    assert not state.get("ledger_checks")
    assert not state["writers_running"]


def test_resume_still_rejects_wrong_tool_database_after_activating_profile(tmp_path):
    result, state = run_resume(tmp_path, {"wrong_db_init_target": True})
    assert result.returncode != 0
    assert "Configured database target mismatch for db-init" in result.stderr
    assert not state.get("built") and not state.get("dry_run")
    assert not state.get("readback") and not state.get("deployed")
    assert not state.get("ledger_checks")
    assert not state["writers_running"]
