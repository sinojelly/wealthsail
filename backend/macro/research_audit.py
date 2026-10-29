"""Optional local diagnostics for public model output, never a READY report."""

from datetime import datetime
import json
import logging
import os
from pathlib import Path
import re
import tempfile
from uuid import UUID

logger = logging.getLogger("wealthsail.research_audit")
_MAX_BYTES = 2 * 1024 * 1024


def _prune_audit_directory(directory: Path, *, max_files: int = 60) -> None:
    """Retain only the most recent audit files to prevent unbounded disk growth.

    Args:
        directory: Path to the audit storage directory.
        max_files: Maximum number of JSON audit logs to keep.
    """
    try:
        files = sorted(
            [p for p in directory.glob("*.json") if p.is_file()],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for old_file in files[max_files:]:
            old_file.unlink(missing_ok=True)
    except Exception as exc:  # noqa: BLE001 -- pruning failure should not fail audit
        logger.warning("research_audit_prune_failed error_class=%s", type(exc).__name__)


def write_research_audit(
    settings,
    run_id,
    *,
    raw,
    source_checks=None,
    stage,
    completed_at,
    error=None,
):
    """Atomically persist public inputs supplied by the worker to a trusted path.

    Only operator configuration selects the directory; model text cannot choose
    a filename. Failure to save diagnostics must not change research validity.
    """
    configured = getattr(settings, "daily_research_audit_dir", "")
    if not configured or not str(configured).strip():
        return False
    temporary = None
    try:
        identity = str(UUID(str(run_id)))
        if not isinstance(stage, str) or not 1 <= len(stage) <= 64:
            raise ValueError("invalid research audit stage")
        if (
            not isinstance(completed_at, datetime)
            or completed_at.tzinfo is None
            or completed_at.utcoffset() is None
        ):
            raise ValueError("research audit requires an aware completion time")
        validation = None
        if error is not None:
            error_type = type(error).__name__
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}", error_type):
                error_type = "ValidationError"
            validation = {"type": error_type, "message": str(error)[:500]}
        payload = {
            "runId": identity,
            "stage": stage,
            "completedAt": completed_at.isoformat(),
            "raw": raw,
            "sourceChecks": source_checks,
            "validationError": validation,
        }
        encoded = json.dumps(
            payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode("utf-8")
        if len(encoded) > _MAX_BYTES:
            raise ValueError("public research audit exceeds size limit")
        directory = Path(configured).expanduser().resolve()
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / f"{identity}.json"
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=directory,
            prefix=f".{identity}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(encoded)
        os.replace(temporary, destination)
        temporary = None
        _prune_audit_directory(directory)
        return True
    except Exception as exc:  # noqa: BLE001 -- optional diagnostics cannot fail research
        logger.warning("research_audit_failed error_class=%s", type(exc).__name__)
        return False
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
