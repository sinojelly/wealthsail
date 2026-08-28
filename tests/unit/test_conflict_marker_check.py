"""Regression tests for the repository merge-conflict marker gate."""

from __future__ import annotations

from scripts.check_conflict_markers import find_conflict_markers


def test_conflict_marker_check_reports_each_unresolved_marker(tmp_path) -> None:
    conflicted = tmp_path / "conflicted.md"
    conflicted.write_text(
        "before\n<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> feature\nafter\n",
        encoding="utf-8",
    )

    findings = find_conflict_markers([conflicted])

    assert [(item.line_number, item.marker) for item in findings] == [
        (2, "<<<<<<< HEAD"),
        (4, "======="),
        (6, ">>>>>>> feature"),
    ]


def test_conflict_marker_check_ignores_non_marker_separators_and_binary(tmp_path) -> None:
    clean = tmp_path / "clean.md"
    clean.write_text(
        "# Heading\n================\nvalue <<<<<<< inline\n>>>>>>>not-a-marker\n",
        encoding="utf-8",
    )
    binary = tmp_path / "asset.bin"
    binary.write_bytes(b"\x00<<<<<<< HEAD\n")

    assert find_conflict_markers([clean, binary]) == []
