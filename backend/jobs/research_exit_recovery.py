"""Query late exit proof; never dispatch, cancel, expire or rewrite a research run.

The runner owns process evidence. Its restart, unavailable receipt, legacy random
call ID or unsupported local CLI keeps the durable guard for manual maintenance.
"""

import asyncio
from contextlib import suppress
import logging
from uuid import NAMESPACE_URL, UUID, uuid5

from backend.common.database import system_connection
from backend.dependencies import now_shanghai
from backend.integrations import codex_client
from backend.integrations.research_lifecycle import sanitize_lifecycle
from backend.integrations.research_process import (
    forget_local_research_exit,
    local_research_exit_proof,
    local_research_exit_status,
)
from backend.macro.model_dispatch_repository import (
    release_model_guard,
    record_model_exit_evidence,
)
from backend.macro.research_diagnostics import safe_error_class

logger = logging.getLogger("wealthsail.research_exit_recovery")
BATCH_LIMIT = 4
POLL_SECONDS = 60


def invocation_identity(run_id, owner_token):
    """Reconstruct the only permitted invocation for one durable run/owner pair."""

    def canonical(value):
        if not isinstance(value, (str, UUID)):
            raise ValueError("invalid research invocation identity")
        try:
            return str(UUID(value)) if isinstance(value, str) else str(value)
        except (ValueError, AttributeError) as exc:
            raise ValueError("invalid research invocation identity") from exc

    run, owner = canonical(run_id), canonical(owner_token)
    call = uuid5(
        NAMESPACE_URL, "wealthsail:research-invocation:v1:" + run + ":" + owner
    )
    return {"runId": run, "ownerToken": owner, "callId": str(call)}


async def recover_research_exits(pool, settings):
    """At most four status reads; only a matching affirmative proof permits CAS."""
    summary = {
        "status": "CHECKED",
        "checked": 0,
        "released": 0,
        "pending": 0,
        "errors": 0,
    }
    remote = bool(getattr(settings, "codex_runner_url", ""))
    if not remote:
        summary.update(status="LOCAL_CHECKED", evidenceStates={})
    async with system_connection(pool) as conn:
        rows = await conn.fetch(
            """SELECT run_id,owner_token FROM research.daily_research_model_guards
            WHERE released_at IS NULL ORDER BY acquired_at,run_id LIMIT $1""",
            BATCH_LIMIT,
        )
    # Never hold a DB connection or transaction while talking to the runner.
    for row in rows:
        summary["checked"] += 1
        try:
            identity = invocation_identity(row["run_id"], row["owner_token"])
            if remote:
                received = await codex_client.research_status(settings, identity)
            else:
                diagnosis = local_research_exit_status(identity)
                code = diagnosis["evidenceState"]
                summary["evidenceStates"][code] = (
                    summary["evidenceStates"].get(code, 0) + 1
                )
                async with system_connection(pool) as conn:
                    await record_model_exit_evidence(
                        conn,
                        row["run_id"],
                        row["owner_token"],
                        {
                            **diagnosis,
                            "checkedAt": now_shanghai().isoformat(),
                        },
                    )
                received = local_research_exit_proof(identity)
            proof = sanitize_lifecycle(received, identity)
            if proof is None or not proof["exitConfirmed"]:
                summary["pending"] += 1
                continue
            async with system_connection(pool) as conn:
                released = await release_model_guard(
                    conn,
                    row["run_id"],
                    row["owner_token"],
                    now_shanghai(),
                    proof={
                        "kind": "REMOTE_RUNNER" if remote else "LOCAL_WINDOWS_JOB",
                        "identityBinding": proof["identityBinding"],
                        "completedAt": proof.get("completedAt"),
                    },
                )
            summary["released"] += int(released)
            if released and not remote:
                forget_local_research_exit(identity)
        except Exception as error:  # noqa: BLE001 -- one receipt cannot block another
            summary["errors"] += 1
            logger.warning(
                "research_exit_recovery_item status=FAILED error_class=%s",
                safe_error_class(type(error).__name__),
            )
    if summary["checked"]:
        logger.info(
            "research_exit_recovery status=CHECKED checked=%s released=%s pending=%s errors=%s",
            summary["checked"],
            summary["released"],
            summary["pending"],
            summary["errors"],
        )
    return summary


async def _wait(stop_event, seconds):
    with suppress(TimeoutError):
        await asyncio.wait_for(stop_event.wait(), seconds)


async def run_research_exit_recovery_service(
    pool, settings, *, stop_event=None, wait=_wait
):
    stop_event = stop_event or asyncio.Event()
    while not stop_event.is_set():
        try:
            await recover_research_exits(pool, settings)
        except Exception as error:  # noqa: BLE001 -- independent recovery only
            logger.warning(
                "research_exit_recovery_service status=FAILED error_class=%s",
                safe_error_class(type(error).__name__),
            )
        await wait(stop_event, POLL_SECONDS)
