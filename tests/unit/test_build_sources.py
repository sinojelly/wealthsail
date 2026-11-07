"""Real Git repositories verify the build-input guard, without Docker or networking."""

import os
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


REPOSITORY = Path(__file__).resolve().parents[2]
HELPER = REPOSITORY / "scripts/check_build_sources.py"


def git(root, *args):
    return subprocess.run(
        ["git", "-c", "core.autocrlf=false", "-C", str(root), *args],
        check=True,
        capture_output=True,
    )


def write(root, name, text="local change\n"):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def repository(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    for name in (
        "backend/Dockerfile",
        "frontend/Dockerfile",
        ".dockerignore",
        "frontend/.dockerignore",
    ):
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPOSITORY / name, root / name)
    for name in (
        "requirements.txt",
        "backend/app.py",
        "migrations/001.sql",
        "scripts/run.py",
        "deployment/codex/extract-native.mjs",
        "deployment/codex/config.toml",
        "frontend/src/main.ts",
        "frontend/package.json",
        "frontend/pnpm-lock.yaml",
        "frontend/nginx.conf",
        "docs/tracked.md",
        "docker-compose.yml",
        "docker-compose.nas.yml",
        "build_docker_wealthsail.sh",
    ):
        write(root, name, "baseline\n")
    write(
        root,
        ".gitignore",
        "*.local.py\nprivate-source/\nnode_modules/\ndist/\ncoverage/\n"
        ".pnpm-store/\n*.tsbuildinfo\n__pycache__/\n*.pyc\n*.log\n"
        ".env*\nauth.json\n.codex/\ntemp/\n",
    )
    git(root, "init", "--quiet")
    git(root, "config", "user.email", "build-test@example.invalid")
    git(root, "config", "user.name", "Build guard tests")
    git(root, "add", ".")
    git(root, "commit", "--quiet", "-m", "fixture")
    return root


def check(root):
    return subprocess.run(
        [sys.executable, str(HELPER), "--root", str(root)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        timeout=30,
    )


def test_clean_inputs_allow_unrelated_review_docs_patch(repository):
    for name in (
        "ai-review/review.md",
        "docs/new.md",
        "fix.patch",
        "external-access/notes.md",
    ):
        write(repository, name)
    write(repository, "docs/tracked.md")
    git(repository, "add", "docs")
    assert check(repository).returncode == 0


@pytest.mark.parametrize(
    "name",
    [
        "requirements.txt",
        "backend/app.py",
        "migrations/001.sql",
        "scripts/run.py",
        "deployment/codex/extract-native.mjs",
        "deployment/codex/config.toml",
        "frontend/src/main.ts",
        "frontend/package.json",
        "frontend/pnpm-lock.yaml",
        "frontend/nginx.conf",
        "backend/Dockerfile",
        "frontend/Dockerfile",
        ".dockerignore",
        "frontend/.dockerignore",
        "docker-compose.yml",
        "docker-compose.nas.yml",
        "build_docker_wealthsail.sh",
    ],
)
@pytest.mark.parametrize("state", ["unstaged", "staged", "deleted"])
def test_dirty_build_inputs_rejected(repository, name, state):
    if state == "deleted":
        (repository / name).unlink()
    else:
        with (repository / name).open("a", encoding="utf-8") as stream:
            stream.write("\n# local edit\n")
        if state == "staged":
            git(repository, "add", name)
    result = check(repository)
    assert result.returncode != 0
    assert name in result.stdout + result.stderr
    assert "baseline" not in result.stdout + result.stderr


def test_index_change_reversed_only_in_worktree_still_rejected(repository):
    write(repository, "backend/app.py", "staged change\n")
    git(repository, "add", "backend/app.py")
    write(repository, "backend/app.py", "baseline\n")
    assert check(repository).returncode != 0


@pytest.mark.parametrize(
    "source,target",
    [
        ("backend/app.py", "docs/moved.py"),
        ("docs/tracked.md", "frontend/src/moved.md"),
    ],
)
def test_renames_across_input_boundary_rejected(repository, source, target):
    (repository / target).parent.mkdir(parents=True, exist_ok=True)
    git(repository, "mv", source, target)
    assert check(repository).returncode != 0


@pytest.mark.parametrize(
    "name",
    [
        "backend/new.py",
        "scripts/new.py",
        "frontend/src/新 source.ts",
        "backend/new.local.py",
        "backend/private-source/hidden.py",
        "frontend/src/ignored.local.py",
        "frontend/docs/review.md",
    ],
)
def test_new_sources_including_gitignored_are_rejected(repository, name):
    write(repository, name, "PRIVATE_CONTENT_DO_NOT_PRINT\n")
    result = check(repository)
    assert result.returncode != 0
    assert json.dumps(name, ensure_ascii=True) in result.stdout + result.stderr
    assert "PRIVATE_CONTENT_DO_NOT_PRINT" not in result.stdout + result.stderr


def test_docker_excluded_cache_and_credentials_are_not_inputs(repository):
    for name in (
        "frontend/node_modules/pkg/index.js",
        "frontend/dist/index.html",
        "frontend/coverage/report.html",
        "frontend/.pnpm-store/pkg/index.js",
        "frontend/tsconfig.tsbuildinfo",
        "frontend/temp/node-compile-cache/data",
        "frontend/.env.production",
        "frontend/.codex/auth.json",
        "frontend/src/.env.local",
        "frontend/src/auth.json",
        "backend/.env.local",
        "backend/__pycache__/app.pyc",
        "scripts/local.log",
        "backend/static/avatars/local-user.png",
    ):
        write(repository, name, "DO_NOT_READ\n")
    assert check(repository).returncode == 0


@pytest.mark.parametrize(
    "name,addition",
    [
        ("backend/Dockerfile", "\nCOPY docs/ ./docs/\n"),
        ("frontend/Dockerfile", "\nADD extra /extra\n"),
        (
            "backend/Dockerfile",
            "\nRUN --mount=type=bind,source=docs,target=/docs cat /docs/extra\n",
        ),
        ("frontend/Dockerfile", "\n#escape=`\n"),
        (".dockerignore", "\n!backend/secret.py\n"),
        ("frontend/.dockerignore", "\nnew-source-dir\n"),
    ],
)
def test_committed_unknown_copy_or_ignore_layout_rejected(repository, name, addition):
    with (repository / name).open("a", encoding="utf-8") as stream:
        stream.write(addition)
    git(repository, "add", name)
    git(repository, "commit", "--quiet", "-m", "unsupported layout")
    assert check(repository).returncode != 0


@pytest.mark.parametrize(
    "name", ["backend/Dockerfile.dockerignore", "frontend/Dockerfile.dockerignore"]
)
def test_dockerfile_specific_ignore_override_rejected(repository, name):
    write(repository, name, "*\n")
    assert check(repository).returncode != 0


def test_git_failure_does_not_succeed(tmp_path):
    assert check(tmp_path).returncode != 0


def test_new_repository_without_head_does_not_succeed(repository):
    git(repository, "checkout", "--orphan", "unborn")
    assert check(repository).returncode != 0


def test_submodule_in_build_inputs_is_explicitly_unsupported(repository):
    head = git(repository, "rev-parse", "HEAD").stdout.decode().strip()
    git(
        repository,
        "update-index",
        "--add",
        "--cacheinfo",
        "160000",
        head,
        "backend/vendor",
    )
    git(repository, "commit", "--quiet", "-m", "gitlink fixture")
    result = check(repository)
    assert result.returncode != 0
    assert "submodule" in result.stderr


def test_tracked_docker_excluded_file_does_not_block(repository):
    write(repository, "frontend/scripts/local.ts")
    git(repository, "add", "frontend/scripts/local.ts")
    git(repository, "commit", "--quiet", "-m", "excluded file")
    write(repository, "frontend/scripts/local.ts", "changed\n")
    assert check(repository).returncode == 0


@pytest.mark.parametrize("flag", ["--assume-unchanged", "--skip-worktree"])
def test_git_flags_cannot_hide_dirty_build_source(repository, flag):
    git(repository, "update-index", flag, "backend/app.py")
    write(repository, "backend/app.py", "hidden worktree change\n")
    result = check(repository)
    assert result.returncode != 0
    assert "backend/app.py" in result.stderr
