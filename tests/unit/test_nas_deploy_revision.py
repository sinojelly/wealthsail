"""Run the deployment script against inert local commands, never Docker or NAS.

Only a temporary script copy executes. BASH_ENV captures every sudo/git call and
routes it to a fixture dispatcher; an unexpected command fails closed.
"""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
HEAD = "a" * 40
OTHER_HEAD = "b" * 40
IMAGE = "sha256:" + "1" * 64
OTHER_IMAGE = "sha256:" + "2" * 64
BASH = shutil.which("bash") if os.name != "nt" else r"C:\Program Files\Git\bin\bash.exe"
pytestmark = pytest.mark.skipif(
    not BASH or not Path(BASH).is_file(), reason="local Bash fixture required"
)


DISPATCHER = r"""
import json, os, pathlib, sys
sys.stdout.reconfigure(newline="\n")
state_path = pathlib.Path(os.environ["FIXTURE_STATE"])
state = json.loads(state_path.read_text())
config = state["scenario"]
kind, args = sys.argv[1], sys.argv[2:]
state["calls"].append([kind, *args])
def save(): state_path.write_text(json.dumps(state))
def finish(value="", code=0):
 save()
 if value != "": print(value)
 raise SystemExit(code)
head, other = "a" * 40, "b" * 40
image, old_image = "sha256:" + "1" * 64, "sha256:" + "2" * 64
frontend_image, old_frontend_image = "sha256:" + "3" * 64, "sha256:" + "4" * 64
if kind == "sources":
 if config.get("sources_error"): finish(code=2)
 if (config.get("dirty_sources") or
     (config.get("dirty_after_build") and state.get("built")) or
     (config.get("dirty_after_up") and state.get("up"))): finish(code=1)
 finish()
if kind == "git":
 if config.get("git_unavailable"): finish(code=128)
 changed = ((config.get("head_changes_after_build") and state.get("built")) or
            (config.get("head_changes_after_up") and state.get("up")))
 finish(other if changed else head)
if kind == "compose":
 if not args or args.pop(0) != "ws": finish(code=96)
 command = args.pop(0)
 if command == "config":
  if "--quiet" in args: finish()
  root = os.environ["FIXTURE_ROOT"]
  merged = {"name":"wealthsail", "services":{
   "api":{"image":"wealthsail-backend:latest","build":{"context":root},"networks":{"postgres_shared":{}}},
   "worker":{"image":"wealthsail-backend:latest"},"codex-runner":{"image":"wealthsail-backend:latest"},
   "frontend":{"image":"wealthsail-frontend:latest","build":{"context":root+"/frontend"},"networks":{"wealth_edge":{}}}},
   "volumes":{
    "avatar_data":{"driver_opts":{"device":state["expected_avatars"]}},
    "codex_auth":{"driver_opts":{"device":state["expected_auth"]}}}}
  if config.get("foreign_image_service"):
   merged["services"][config["foreign_image_service"]]["image"] = "wealthsail-backend:stale-fixture"
  finish(json.dumps(merged))
 if command == "build":
  state["built"] = True
  state["build_commit"] = os.environ.get("BUILD_COMMIT")
  state["tag_revision"] = other if config.get("build_wrong_revision") else os.environ.get("BUILD_COMMIT", "unknown")
  state["frontend_tag_revision"] = other if config.get("build_wrong_frontend_revision") else os.environ.get("BUILD_COMMIT", "unknown")
  finish()
 if command == "up":
  state["up"] = True
  finish()
 if command == "ps":
  if "-q" not in args: finish()
  service = args[-1]
  if not state.get("up"): finish()
  if config.get("container_list_error") == service: finish("fixture-"+service, code=1)
  if config.get("replace_service_after_check") == service and service in state.get("inspected_services", []): finish("fixture-"+service+"-replacement")
  if config.get("missing_service") == service: finish()
  if config.get("oneoff_only_service") == service: finish("fixture-"+service+"-oneoff")
  if config.get("extra_oneoff_instance") == service: finish("fixture-"+service+"\nfixture-"+service+"-oneoff")
  if config.get("old_extra_instance") == service: finish("fixture-"+service+"\nfixture-"+service+"-old")
  if config.get("duplicate_same_instance") == service: finish("fixture-"+service+"\nfixture-"+service+"-second")
  finish("fixture-"+service)
 if command == "exec": finish()
 finish(code=96)
if kind != "docker": finish(code=96)
if args and args[0] == "ps": finish()
if args[:2] == ["image", "inspect"]:
 target = args[2]
 if target == "wealthsail-backend:latest":
  if config.get("tag_missing"): finish(code=1)
  revision = state.get("tag_revision", config.get("tag_revision", head))
  selected = old_image if config.get("tag_drifts_after_up") and state.get("up") else image
  finish(selected + " " + revision, code=1 if config.get("tag_inspect_error") else 0)
 if target == "wealthsail-frontend:latest":
  if config.get("frontend_tag_missing"): finish(code=1)
  revision = state.get("frontend_tag_revision", config.get("frontend_tag_revision", head))
  selected = old_frontend_image if config.get("frontend_tag_drifts_after_up") and state.get("up") else frontend_image
  finish(selected + " " + revision, code=1 if config.get("frontend_tag_inspect_error") else 0)
 if target in {image, old_image}:
  finish(other if target == old_image else config.get("actual_image_revision", head), code=1 if config.get("runtime_image_inspect_error") else 0)
 if target in {frontend_image, old_frontend_image}:
  finish(other if target == old_frontend_image else config.get("frontend_actual_image_revision", head))
 finish(code=96)
if args[:2] == ["container", "inspect"]:
 target = args[2]
 if not target.startswith("fixture-"): finish(code=96)
 service = target.removeprefix("fixture-")
 oneoff = service.endswith("-oneoff")
 old = oneoff or service.endswith("-old") or config.get("old_service") == service
 service = service.removesuffix("-old").removesuffix("-oneoff").removesuffix("-second")
 running = "false" if oneoff or config.get("stopped_service") == service else "true"
 project = "foreign-project" if config.get("foreign_project") == service else "wealthsail"
 label = "foreign-service" if config.get("foreign_service") == service else service
 oneoff_label = "True" if oneoff else ("unknown" if config.get("unknown_oneoff_service") == service else "False")
 state.setdefault("inspected_services", []).append(service)
 selected_image = (old_frontend_image if old else frontend_image) if service == "frontend" else (old_image if old else image)
 finish(" ".join((selected_image,running,project,label,oneoff_label)), code=1 if config.get("container_inspect_error") == service else 0)
finish(code=96)
"""


SHELL_ENV = r"""
fixture_dispatch() { MSYS_NO_PATHCONV=1 "$FIXTURE_PY" "$FIXTURE_DISPATCHER" "$@"; }
git() { fixture_dispatch git "$@"; }
docker() { fixture_dispatch docker "$@"; }
sudo() {
 if [[ "${1:-}" == env ]]; then
  shift
  while [[ "${1:-}" == *=* ]]; do export "$1"; shift; done
 fi
 case "${1:-}" in
  docker) shift; fixture_dispatch docker "$@" ;;
  "$NAS_TOOLS/nas-compose") shift; fixture_dispatch compose "$@" ;;
  *) printf '%s\n' 'Fixture rejected unexpected privileged command' >&2; return 97 ;;
 esac
}
python3() {
 if [[ "${1:-}" == */scripts/check_build_sources.py ]]; then
  shift
  fixture_dispatch sources "$@"
  return
 fi
 local arg
 local -a native=()
 for arg in "$@"; do
  if [[ "$FIXTURE_WINDOWS" == 1 && "$arg" =~ ^/[a-zA-Z]/ ]]; then
   native+=("$(cygpath -w "$arg")")
  else native+=("$arg"); fi
 done
 MSYS_NO_PATHCONV=1 "$FIXTURE_PY" "${native[@]}"
}
"""


def run_script(tmp_path, *, mode=None, scenario=None):
    root = tmp_path / "checkout"
    tools = tmp_path / "tools"
    root.mkdir()
    tools.mkdir()
    (root / "frontend").mkdir()
    script = root / "build_docker_wealthsail.sh"
    source = (ROOT / "build_docker_wealthsail.sh").read_bytes()
    script.write_bytes(source)
    wrapper = tools / "nas-compose"
    wrapper.write_text("#!/usr/bin/env bash\nexit 97\n", encoding="utf-8")
    wrapper.chmod(0o700)
    (tools / "deploy.json").write_text(
        json.dumps({"wealth": root.as_posix()}), encoding="utf-8"
    )
    state = tmp_path / "state.json"
    # Derive existing safety constants without copying personal deployment paths
    # into Git or changing the real configuration.
    source_text = source.decode("utf-8")
    expected_paths = {
        key: re.search(r"(?m)^" + variable + r"='([^']+)'$", source_text).group(1)
        for key, variable in (
            ("expected_avatars", "EXPECTED_AVATARS"),
            ("expected_auth", "EXPECTED_CODEX_AUTH"),
        )
    }
    state.write_text(
        json.dumps({"scenario": scenario or {}, "calls": [], **expected_paths}),
        encoding="utf-8",
    )
    dispatcher, shell_env = tmp_path / "dispatch.py", tmp_path / "shell-env.sh"
    dispatcher.write_text(DISPATCHER, encoding="utf-8")
    shell_env.write_text(SHELL_ENV, encoding="utf-8")
    # No caller Docker, NAS, login or shell initialization settings are inherited.
    env = {
        key: value
        for key, value in os.environ.items()
        if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "COMSPEC"}
    }
    env.update(
        {
            "BASH_ENV": shell_env.as_posix(),
            "NAS_TOOLS": tools.as_posix(),
            "FIXTURE_STATE": state.as_posix(),
            "FIXTURE_ROOT": root.as_posix(),
            "FIXTURE_PY": Path(sys.executable).as_posix(),
            "FIXTURE_DISPATCHER": dispatcher.as_posix(),
            "FIXTURE_WINDOWS": "1" if os.name == "nt" else "0",
            "MSYS_NO_PATHCONV": "1",
            "MSYS2_ENV_CONV_EXCL": "*",
        }
    )
    result = subprocess.run(
        [BASH, "--noprofile", "--norc", str(script), *([mode] if mode else [])],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )
    return result, json.loads(state.read_text(encoding="utf-8"))


def completed(result):
    return "WealthSail deployment checks complete." in result.stdout


def test_default_deploy_requires_explicit_no_build_and_checks_every_runtime_image(
    tmp_path,
):
    result, state = run_script(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert state["build_commit"] == HEAD
    up = next(call for call in state["calls"] if call[:3] == ["compose", "ws", "up"])
    assert "--no-build" in up
    events = [
        call[0]
        if call[0] == "sources"
        else call[2]
        if call[:2] == ["compose", "ws"]
        else call[0]
        for call in state["calls"]
    ]
    assert events.index("sources") < events.index("build")
    assert "sources" in events[events.index("up") + 1 :]
    for service in ("api", "worker", "codex-runner", "frontend"):
        assert any(
            call[:4] == ["docker", "container", "inspect", "fixture-" + service]
            for call in state["calls"]
        )
    assert completed(result)


@pytest.mark.parametrize("mode", [None, "--build-only"])
@pytest.mark.parametrize(
    "scenario",
    [
        {"build_wrong_revision": True},
        {"build_wrong_frontend_revision": True},
        {"head_changes_after_build": True},
    ],
)
def test_build_revision_failure_prevents_up_and_success_message(
    tmp_path, mode, scenario
):
    result, state = run_script(tmp_path, mode=mode, scenario=scenario)
    assert result.returncode != 0
    assert state.get("built") is True and not state.get("up")
    assert not completed(result) and "Images ready" not in result.stdout


@pytest.mark.parametrize(
    "scenario",
    [{"tag_revision": OTHER_HEAD}, {"tag_revision": "unknown"}, {"tag_missing": True}],
)
def test_deploy_only_refuses_unknown_missing_or_wrong_tag_before_up(tmp_path, scenario):
    result, state = run_script(tmp_path, mode="--deploy-only", scenario=scenario)
    assert result.returncode != 0
    assert not state.get("built") and not state.get("up")
    assert not completed(result)


@pytest.mark.parametrize(
    "scenario",
    [
        {"missing_service": "worker"},
        {"stopped_service": "api"},
        {"old_service": "codex-runner"},
        {"actual_image_revision": OTHER_HEAD},
        {"foreign_project": "worker"},
        {"foreign_service": "api"},
        {"old_extra_instance": "worker"},
        {"head_changes_after_up": True},
        {"tag_drifts_after_up": True},
        {"oneoff_only_service": "api"},
        {"unknown_oneoff_service": "worker"},
        {"missing_service": "frontend"},
        {"stopped_service": "frontend"},
        {"frontend_tag_drifts_after_up": True},
    ],
)
def test_runtime_mismatch_fails_verification_without_claiming_completed_deployment(
    tmp_path, scenario
):
    result, state = run_script(tmp_path, mode="--deploy-only", scenario=scenario)
    assert state.get("up") is True
    assert result.returncode != 0
    assert not completed(result)


def test_build_only_validates_image_without_starting_containers(tmp_path):
    result, state = run_script(tmp_path, mode="--build-only")
    assert result.returncode == 0, result.stdout + result.stderr
    assert state["build_commit"] == HEAD and not state.get("up")
    for tag in ("wealthsail-backend:latest", "wealthsail-frontend:latest"):
        assert any(
            call[:4] == ["docker", "image", "inspect", tag] for call in state["calls"]
        )
    assert "Images ready" in result.stdout and not completed(result)


def test_historical_oneoff_container_is_excluded_but_running_service_is_still_checked(
    tmp_path,
):
    result, state = run_script(
        tmp_path, mode="--deploy-only", scenario={"extra_oneoff_instance": "api"}
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert state.get("up") is True and completed(result)
    checked = [
        call[3]
        for call in state["calls"]
        if call[:3] == ["docker", "container", "inspect"]
    ]
    assert {
        "fixture-api",
        "fixture-api-oneoff",
        "fixture-worker",
        "fixture-codex-runner",
    } <= set(checked)


@pytest.mark.parametrize(
    "scenario",
    [
        {"tag_inspect_error": True},
        {"runtime_image_inspect_error": True},
        {"container_inspect_error": "worker"},
        {"container_list_error": "api"},
    ],
)
def test_inspect_failure_even_with_valid_looking_stdout_cannot_claim_success(
    tmp_path, scenario
):
    result, _state = run_script(tmp_path, mode="--deploy-only", scenario=scenario)
    assert result.returncode != 0
    assert not completed(result)


def test_container_replaced_during_verification_cannot_claim_completed_deployment(
    tmp_path,
):
    result, state = run_script(
        tmp_path,
        mode="--deploy-only",
        scenario={"replace_service_after_check": "worker"},
    )
    assert state.get("up") is True
    assert result.returncode != 0
    assert not completed(result)


def test_check_remains_read_only_without_git_or_build_requirement(tmp_path):
    result, state = run_script(
        tmp_path,
        mode="--check",
        scenario={"git_unavailable": True, "tag_missing": True, "dirty_sources": True},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not state.get("built") and not state.get("up")
    assert not any(call[0] == "git" for call in state["calls"])
    assert not any(call[0] == "sources" for call in state["calls"])
    assert "Read-only checks complete." in result.stdout and not completed(result)


@pytest.mark.parametrize("service", ["api", "worker", "codex-runner", "frontend"])
def test_overlay_cannot_select_another_backend_tag_before_any_build_or_up(
    tmp_path, service
):
    result, state = run_script(
        tmp_path, mode="--deploy-only", scenario={"foreign_image_service": service}
    )
    assert result.returncode != 0
    assert not state.get("built") and not state.get("up")
    assert not completed(result)


def test_multiline_tag_metadata_cannot_hide_untrusted_trailing_record(tmp_path):
    result, state = run_script(
        tmp_path, mode="--deploy-only", scenario={"tag_revision": HEAD + "\nextra"}
    )
    assert result.returncode != 0
    assert not state.get("built") and not state.get("up")
    assert not completed(result)


@pytest.mark.parametrize("service", ["api", "worker", "codex-runner", "frontend"])
def test_duplicate_same_version_runtime_is_rejected_instead_of_confirming_multiple_consumers(
    tmp_path,
    service,
):
    result, state = run_script(
        tmp_path, mode="--deploy-only", scenario={"duplicate_same_instance": service}
    )
    assert state.get("up") is True
    assert result.returncode != 0
    assert not completed(result)


@pytest.mark.parametrize(
    "scenario",
    [
        {"old_service": "frontend"},
        {"frontend_actual_image_revision": OTHER_HEAD},
    ],
)
def test_frontend_runtime_must_use_the_verified_frontend_image_and_revision(
    tmp_path, scenario
):
    result, state = run_script(tmp_path, mode="--deploy-only", scenario=scenario)
    assert state.get("up") is True
    assert result.returncode != 0
    assert not completed(result)


def test_frontend_tag_must_match_head_before_deploy_only_up(tmp_path):
    result, state = run_script(
        tmp_path, mode="--deploy-only", scenario={"frontend_tag_revision": OTHER_HEAD}
    )
    assert result.returncode != 0
    assert not state.get("up") and not completed(result)


@pytest.mark.parametrize("mode", [None, "--build-only", "--deploy-only"])
@pytest.mark.parametrize("scenario", [{"dirty_sources": True}, {"sources_error": True}])
def test_dirty_or_unreadable_build_sources_block_before_build_or_up(
    tmp_path, mode, scenario
):
    result, state = run_script(tmp_path, mode=mode, scenario=scenario)
    assert result.returncode != 0
    assert not state.get("built") and not state.get("up")
    assert not completed(result) and "Images ready" not in result.stdout


@pytest.mark.parametrize("mode", [None, "--build-only"])
def test_sources_modified_during_build_cannot_produce_ready_or_deploy_success(
    tmp_path, mode
):
    result, state = run_script(
        tmp_path, mode=mode, scenario={"dirty_after_build": True}
    )
    assert result.returncode != 0
    assert state.get("built") is True and not state.get("up")
    assert not completed(result) and "Images ready" not in result.stdout


def test_sources_modified_after_up_cannot_claim_completed_deployment(tmp_path):
    result, state = run_script(
        tmp_path, mode="--deploy-only", scenario={"dirty_after_up": True}
    )
    assert state.get("up") is True
    assert result.returncode != 0 and not completed(result)
