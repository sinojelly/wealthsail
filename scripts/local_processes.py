"""Small ownership manifest for this checkout's three local services."""

import json
import os
from pathlib import Path

_FIELDS = ("ProcessId", "created", "Name", "ExecutablePath", "CommandLine")


def write_manifest(path, manifest):
    pending = path.with_name(path.name + ".tmp")
    pending.write_text(json.dumps(manifest), encoding="utf-8")
    pending.replace(path)


def read_manifest(path, workspace):
    try:
        data = json.loads(path.read_text("utf-8"))
    except FileNotFoundError:
        return None
    except (ValueError, OSError) as exc:
        raise RuntimeError(
            "Cannot read managed service identities; ownership records retained"
        ) from exc
    try:
        valid = (
            os.path.normcase(str(Path(data["workspace"]).resolve()))
            == os.path.normcase(str(workspace.resolve()))
            and isinstance(data["run_id"], str)
            and bool(data["run_id"])
            and isinstance(data["children"], list)
        )
        for row in data["children"]:
            valid = (
                valid
                and all(row.get(key) is not None for key in _FIELDS)
                and row.get("service") in {"api", "frontend", "macro"}
            )
    except (KeyError, TypeError, ValueError):
        valid = False
    if not valid:
        raise RuntimeError(
            "Invalid service ownership/workspace manifest; records retained"
        )
    return data


def identity_matches(saved, current):
    if current is None:
        return False
    # A proved different birth/name is PID reuse, even if its command is private.
    for key in ("ProcessId", "created", "Name"):
        if current.get(key) is None:
            raise RuntimeError("Process identity unavailable")
        if str(saved[key]).casefold() != str(current[key]).casefold():
            return False
    for key in ("ExecutablePath", "CommandLine"):
        if not current.get(key):
            raise RuntimeError("Process identity unavailable")
    return (
        os.path.normcase(saved["ExecutablePath"])
        == os.path.normcase(current["ExecutablePath"])
        and saved["CommandLine"] == current["CommandLine"]
    )


def remaining_services(path, workspace, query):
    manifest = read_manifest(path, workspace)
    if not manifest:
        return []
    return [
        row
        for row in manifest["children"]
        if identity_matches(row, query(row["ProcessId"]))
    ]


def record_services(path, workspace, run_id, roots, inventory):
    previous = read_manifest(path, workspace)
    if previous and previous["run_id"] != run_id and previous["children"]:
        raise RuntimeError("Unclean managed services remain; use -Action Stop")
    known = {row["ProcessId"]: row for row in (previous or {}).get("children", [])}
    anchors = {row["ProcessId"]: row for row in (previous or {}).get("roots", [])}
    current = {row["ProcessId"]: row for row in inventory}
    selected = {}
    for pid, service in roots.items():
        row = current.get(pid)
        anchor = anchors.get(pid) or known.get(pid)
        if row and (anchor is None or identity_matches(anchor, row)):
            selected[pid] = service
            anchors.setdefault(pid, {**row, "service": service})
    # Retain an already-recorded real Python child after its redirector exits.
    for pid, old in known.items():
        if identity_matches(old, current.get(pid)):
            selected[pid] = old["service"]
    while True:
        children = {
            pid: selected[row.get("ParentProcessId")]
            for pid, row in current.items()
            if pid not in selected
            and row.get("ParentProcessId") in selected
            and row.get("created") is not None
            and row["created"] >= current[row["ParentProcessId"]]["created"]
        }
        if not children:
            break
        selected.update(children)
    rows = []
    for pid, service in selected.items():
        row = current[pid]
        if not all(row.get(key) is not None for key in _FIELDS):
            raise RuntimeError("Cannot verify managed child process identity")
        rows.append(
            {
                **{key: row.get(key) for key in (*_FIELDS, "ParentProcessId")},
                "service": service,
            }
        )
    manifest = {
        "workspace": str(workspace.resolve()),
        "run_id": run_id,
        "children": rows,
        "roots": list(anchors.values()),
    }
    write_manifest(path, manifest)
    return manifest


def cleanup_services(path, workspace, query, terminate):
    manifest = read_manifest(path, workspace)
    if not manifest:
        return []
    remaining = []
    for row in reversed(manifest["children"]):
        try:
            if not identity_matches(row, query(row["ProcessId"])):
                continue
            try:
                terminate(row["ProcessId"])
            except Exception:  # remaining processes must still get their own cleanup
                pass
            if identity_matches(row, query(row["ProcessId"])):
                remaining.append(row)
        except Exception:  # unreadable is unknown, never proof of disappearance
            remaining.append(row)
    remaining.reverse()
    if remaining:
        write_manifest(path, {**manifest, "children": remaining})
    else:
        path.unlink(missing_ok=True)
    return remaining
