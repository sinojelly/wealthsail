#!/usr/bin/env python3
"""Reject uncommitted inputs to WealthSail's two supported image builds.

This deliberately supports the current COPY/ignore manifest, not arbitrary
Dockerfiles. Update this manifest and its real-Git tests when build inputs change.
Only paths and control-file syntax are inspected; source/credential contents are
never read. This does not promise reproducible remote package downloads.
"""

import argparse
import fnmatch
import json
import os
from pathlib import Path
import subprocess
import sys


INPUTS = (
    ".dockerignore",
    "requirements.txt",
    "backend",
    "migrations",
    "scripts",
    "deployment/codex/extract-native.mjs",
    "deployment/codex/config.toml",
    "frontend",
    "docker-compose.yml",
    "docker-compose.nas.yml",
    "build_docker_wealthsail.sh",
)
ROOT_IGNORE = (
    ".venv",
    "**/__pycache__",
    "**/*.pyc",
    "**/*.pyo",
    ".env",
    "*.log",
    ".git",
    ".gitignore",
    "docs",
    "tests",
    "frontend/node_modules",
    "frontend/dist",
    "frontend/scripts",
    "backend/.env",
    "**/*.env",
    "**/.env*",
    "**/.codex",
    "**/auth.json",
    ".local",
    ".venv-local",
    ".superpowers",
    "artifacts",
    "backups",
    "ai-review",
    "temp",
    "external-access",
    "**/*.log",
    "backend/static/avatars",
)
FRONTEND_IGNORE = (
    "node_modules",
    "dist",
    "scripts",
    "*.log",
    ".env",
    ".pnpm-store",
    "coverage",
    "**/*.tsbuildinfo",
    "temp",
    "**/.env*",
    "**/*.env",
    "**/.codex",
    "**/auth.json",
    "**/*.log",
)
COPY_MANIFEST = {
    "backend/Dockerfile": (
        "COPY deployment/codex/extract-native.mjs /build/extract-native.mjs",
        "COPY --from=codex-tooling /opt/codex/ /opt/codex/",
        "COPY requirements.txt .",
        "COPY backend/ ./backend/",
        "COPY migrations/ ./migrations/",
        "COPY scripts/ ./scripts/",
        "COPY --chown=10001:10001 deployment/codex/config.toml /home/appuser/.codex/config.toml",
    ),
    "frontend/Dockerfile": (
        "COPY package.json pnpm-lock.yaml ./",
        "COPY . .",
        "COPY nginx.conf /etc/nginx/conf.d/default.conf",
        "COPY --from=build /app/dist /usr/share/nginx/html",
    ),
}


class CheckError(Exception):
    """A failed check must never be interpreted as clean inputs."""


def _git(root, *args):
    # Do not let inherited index/worktree overrides inspect a different checkout.
    env = {
        key: value for key, value in os.environ.items() if not key.startswith("GIT_")
    }
    try:
        result = subprocess.run(
            ["git", "-c", "core.fsmonitor=false", "-C", str(root), *args],
            capture_output=True,
            timeout=30,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise CheckError("Git query unavailable or timed out") from error
    if result.returncode != 0:
        raise CheckError("Git query failed (exit %s)" % result.returncode)
    return result.stdout


def _lines(root, name):
    path = root / name
    if path.is_symlink():
        raise CheckError("Unsupported symlink control file: " + name)
    try:
        return path.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeError) as error:
        raise CheckError("Cannot read build control file: " + name) from error


def _validate_layout(root):
    for name, expected in COPY_MANIFEST.items():
        override = name + ".dockerignore"
        if (root / override).exists() or (root / override).is_symlink():
            raise CheckError("Unsupported Dockerfile ignore override: " + override)
        copies = []
        continued = ""
        for raw in _lines(root, name):
            line = raw.strip()
            if not line or line.startswith("#"):
                if line[1:].strip().lower().startswith("escape"):
                    raise CheckError("Unsupported Dockerfile escape directive: " + name)
                continue
            continued += " " + line
            if continued.endswith("\\"):
                continued = continued[:-1]
                continue
            instruction = " ".join(continued.split())
            command = instruction.split(" ", 1)[0].upper()
            if command == "RUN" and "--mount=" in instruction:
                raise CheckError(
                    "Unsupported Dockerfile mount; review build manifest: " + name
                )
            if command in ("COPY", "ADD", "ONBUILD"):
                copies.append(command + instruction[len(command) :])
            continued = ""
        if continued or tuple(copies) != expected:
            raise CheckError(
                "Unsupported COPY/ADD layout; update build manifest: " + name
            )
    for name, expected in (
        (".dockerignore", ROOT_IGNORE),
        ("frontend/.dockerignore", FRONTEND_IGNORE),
    ):
        patterns = tuple(
            line.strip()
            for line in _lines(root, name)
            if line.strip() and not line.strip().startswith("#")
        )
        if patterns != expected:
            raise CheckError(
                "Unsupported ignore layout; update build manifest: " + name
            )


def _ignored(path, patterns):
    """Match only the finite, validated patterns above (no negation support)."""
    parts = path.split("/")
    for pattern in patterns:
        if pattern.startswith("**/"):
            if any(fnmatch.fnmatchcase(part, pattern[3:]) for part in parts):
                return True
        else:
            pattern_parts = pattern.split("/")
            if len(parts) >= len(pattern_parts) and all(
                fnmatch.fnmatchcase(part, match)
                for part, match in zip(parts, pattern_parts)
            ):
                return True
    return False


def _is_input(path):
    if path.startswith("frontend/"):
        return not _ignored(path[len("frontend/") :], FRONTEND_IGNORE)
    return any(
        path == name or path.startswith(name + "/") for name in INPUTS
    ) and not _ignored(path, ROOT_IGNORE)


def _untracked_exclusions():
    # Prune large caches in Git itself. Root ** patterns apply only to backend
    # COPY directories, while the frontend has its own independent context.
    patterns = []
    for pattern in ROOT_IGNORE:
        if pattern.startswith("**/"):
            patterns.extend(
                prefix + "/" + pattern
                for prefix in ("backend", "migrations", "scripts")
            )
        elif not pattern.startswith("frontend/"):
            patterns.append(pattern)
    patterns.extend("frontend/" + pattern for pattern in FRONTEND_IGNORE)
    return [
        spec
        for pattern in patterns
        for spec in (
            ":(top,exclude,glob)" + pattern,
            ":(top,exclude,glob)" + pattern + "/**",
        )
    ]


def dirty_build_sources(root):
    root = root.resolve()
    top = Path(
        os.fsdecode(_git(root, "rev-parse", "--show-toplevel")).strip()
    ).resolve()
    if top != root:
        raise CheckError("--root must name the Git repository root")
    _git(root, "rev-parse", "--verify", "HEAD")
    _validate_layout(root)
    staged = _git(root, "ls-files", "--stage", "-z", "--", *INPUTS)
    if any(
        record.startswith(b"160000 ")
        and _is_input(os.fsdecode(record.split(b"\t", 1)[1]))
        for record in staged.split(b"\0")
        if record
    ):
        raise CheckError("Unsupported submodule in build inputs")
    flags = _git(root, "ls-files", "-v", "-z", "--", *INPUTS)
    for record in flags.split(b"\0"):
        if record and (record[:1].islower() or record[:1] == b"S"):
            path = os.fsdecode(record[2:])
            if _is_input(path):
                raise CheckError(
                    "Unsupported hidden Git worktree flag: " + json.dumps(path)
                )
    paths = set()
    # Compare each edge separately: a staged edit reverted only in the worktree
    # must still be rejected. Disable renames to retain both sides of a move.
    for extra in (("--cached", "HEAD"), ()):
        output = _git(
            root,
            "diff",
            "--name-only",
            "-z",
            "--no-renames",
            "--no-ext-diff",
            "--no-textconv",
            *extra,
            "--",
            *INPUTS,
        )
        paths.update(os.fsdecode(path) for path in output.split(b"\0") if path)
    # No --exclude-standard: Gitignored files can still be copied into an image.
    output = _git(
        root, "ls-files", "--others", "-z", "--", *INPUTS, *_untracked_exclusions()
    )
    paths.update(os.fsdecode(path) for path in output.split(b"\0") if path)
    return sorted(path for path in paths if _is_input(path))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        paths = dirty_build_sources(args.root)
    except CheckError as error:
        print("Build source check refused: " + str(error), file=sys.stderr)
        return 2
    if paths:
        print("Build source check refused: uncommitted build inputs", file=sys.stderr)
        for path in paths[:100]:
            print("  " + json.dumps(path, ensure_ascii=True), file=sys.stderr)
        if len(paths) > 100:
            print("  ... %s more paths" % (len(paths) - 100), file=sys.stderr)
        return 1
    print("Build source check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
