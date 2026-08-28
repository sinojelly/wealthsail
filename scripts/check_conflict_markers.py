"""Fail when tracked text files contain unresolved merge-conflict markers."""

from __future__ import annotations

import argparse
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


CONFLICT_MARKER = re.compile(
    r"^(?:<{7}(?: .*)?|={7}|>{7}(?: .*)?|\|{7}(?: .*)?)$"
)


@dataclass(frozen=True)
class ConflictMarker:
    path: Path
    line_number: int
    marker: str


def tracked_files(repo_root: Path) -> list[Path]:
    """Return repository-tracked paths without shell-specific quoting."""
    result = subprocess.run(
        ["git", "-C", str(repo_root), "ls-files", "-z"],
        check=True,
        stdout=subprocess.PIPE,
    )
    return [
        repo_root / Path(raw.decode("utf-8", errors="surrogateescape"))
        for raw in result.stdout.split(b"\0")
        if raw
    ]


def find_conflict_markers(paths: Iterable[Path]) -> list[ConflictMarker]:
    """Inspect text files and report markers that begin an entire line."""
    findings: list[ConflictMarker] = []
    for path in paths:
        if not path.is_file():
            continue
        content = path.read_bytes()
        if b"\0" in content:
            continue
        text = content.decode("utf-8", errors="replace")
        for line_number, line in enumerate(text.splitlines(), start=1):
            if CONFLICT_MARKER.fullmatch(line):
                findings.append(ConflictMarker(path, line_number, line))
    return findings


def _display_path(path: Path, repo_root: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return str(path)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Reject unresolved Git merge-conflict markers in text files."
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Optional files to inspect; defaults to every Git-tracked file.",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository root used for Git discovery and relative diagnostics.",
    )
    args = parser.parse_args(argv)
    repo_root = args.repo_root.resolve()
    paths = [path if path.is_absolute() else repo_root / path for path in args.paths]
    if not paths:
        paths = tracked_files(repo_root)

    findings = find_conflict_markers(paths)
    for finding in findings:
        display = _display_path(finding.path, repo_root)
        print(
            f"{display}:{finding.line_number}: unresolved merge conflict marker: "
            f"{finding.marker}"
        )
    if findings:
        print(f"Conflict marker check failed: {len(findings)} marker(s) found.")
        return 1
    print(f"Conflict marker check passed: {len(paths)} file(s) scanned.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
