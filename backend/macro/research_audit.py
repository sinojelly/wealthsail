"""Optional local diagnostics for public model output, never a READY report."""

from datetime import datetime
import json
import logging
import os
from pathlib import Path
import re
import tempfile
from uuid import UUID

from backend.macro.research_diagnostics import (
    safe_error_class,
    sanitize_failure_diagnostics,
)

logger = logging.getLogger("wealthsail.research_audit")
_MAX_BYTES = 2 * 1024 * 1024


def _prune_audit_directory(
    directory: Path, *, max_files: int = 60, diagnostic: bool = False
) -> None:
    """Retain only the most recent audit files to prevent unbounded disk growth.

    Args:
        directory: Path to the audit storage directory.
        max_files: Maximum files per class (raw reports or small summaries).
    """
    try:
        files = sorted(
            [
                p
                for p in directory.glob("*.json")
                if p.is_file() and p.name.endswith(".diagnostic.json") == diagnostic
            ],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for old_file in files[max_files:]:
            old_file.unlink(missing_ok=True)
    except Exception as exc:  # noqa: BLE001 -- pruning failure should not fail audit
        logger.warning(
            "research_audit_prune_failed error_class=%s",
            safe_error_class(type(exc).__name__),
        )


def _atomic_write(directory, identity, encoded, *, diagnostic=False):
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = None
    try:
        suffix = ".diagnostic.json" if diagnostic else ".json"
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=directory,
            prefix=f".{identity}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            os.chmod(temporary, 0o600)
            handle.write(encoded)
        os.replace(temporary, directory / f"{identity}{suffix}")
        temporary = None
        _prune_audit_directory(directory, diagnostic=diagnostic)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def check_research_audit(settings):
    """Probe optional storage once at worker service startup, without stopping it."""
    if not (
        getattr(settings, "daily_research_enabled", False)
        or getattr(settings, "daily_research_manual_enabled", False)
    ):
        return
    configured = getattr(settings, "daily_research_audit_dir", "")
    if not configured or not str(configured).strip():
        logger.warning("daily_research_audit_disabled")
        return
    try:
        directory = Path(configured).expanduser().resolve()
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=directory) as handle:
            handle.write(b"audit storage check")
            handle.flush()
    except Exception:  # noqa: BLE001 -- optional diagnostics cannot stop the service
        logger.warning("daily_research_audit_unavailable reason=NOT_WRITABLE")


def write_research_diagnostic(
    settings,
    run_id,
    *,
    stage,
    error_class,
    report_status,
    completed_at,
    reason_code=None,
    rule_id=None,
    item_index=None,
    missing_industry_codes=None,
):
    """Persist a small closed summary independently of the raw payload size."""
    configured = getattr(settings, "daily_research_audit_dir", "")
    if not configured or not str(configured).strip():
        return False
    try:
        identity = str(UUID(str(run_id)))
        if report_status not in {"READY", "FAILED", "UNCERTAIN"}:
            raise ValueError("invalid research diagnostic status")
        if (
            not isinstance(completed_at, datetime)
            or completed_at.tzinfo is None
            or completed_at.utcoffset() is None
        ):
            raise ValueError("research diagnostic requires an aware completion time")
        diagnostics = sanitize_failure_diagnostics(
            stage=stage,
            reason_code=reason_code,
            rule_id=rule_id,
            item_index=item_index,
            missing_industry_codes=missing_industry_codes
            if report_status != "READY"
            else None,
        )
        payload = {
            "stage": diagnostics["failure_stage"],
            "errorClass": safe_error_class(error_class),
            "reasonCode": diagnostics["failure_reason_code"],
            "ruleId": diagnostics["failure_rule_id"],
            "itemIndex": diagnostics["failure_item_index"],
            "reportStatus": report_status,
        }
        if codes := diagnostics.get("failure_industry_codes"):
            payload["industryCodes"] = codes
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        _atomic_write(
            Path(configured).expanduser().resolve(), identity, encoded, diagnostic=True
        )
        return True
    except Exception as exc:  # noqa: BLE001 -- summary failure cannot fail research
        logger.warning(
            "research_diagnostic_failed error_class=%s",
            safe_error_class(type(exc).__name__),
        )
        return False


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
        _atomic_write(directory, identity, encoded)
        return True
    except Exception as exc:  # noqa: BLE001 -- optional diagnostics cannot fail research
        logger.warning(
            "research_audit_failed error_class=%s", safe_error_class(type(exc).__name__)
        )
        return False
