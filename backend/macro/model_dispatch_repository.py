"""Durable, fail-closed model occupation shared by all public research entries."""

from uuid import UUID, uuid4

from backend.macro.daily_repository import local


class ResearchModelBusy(RuntimeError):
    """An earlier model has not been confirmed to have exited."""


def _require_transaction(conn):
    if not conn.is_in_transaction():
        raise RuntimeError("model dispatch requires a transaction")


async def lock_model_dispatch(conn):
    """Serialize the guard check and immutable run insertion in one transaction."""
    _require_transaction(conn)
    await conn.execute(
        "SELECT pg_advisory_xact_lock(hashtext('research-model-dispatch'))"
    )
    if await conn.fetchval(
        "SELECT EXISTS(SELECT 1 FROM research.daily_research_model_guards WHERE released_at IS NULL)"
    ):
        raise ResearchModelBusy("research model exit has not been confirmed")


async def create_model_guard(conn, run_id, now):
    """Commit occupation together with the caller's immutable run cost barrier."""
    _require_transaction(conn)
    token = uuid4()
    inserted = await conn.fetchval(
        """INSERT INTO research.daily_research_model_guards(run_id,owner_token,acquired_at)
        SELECT run_id,$2,$3 FROM research.daily_research_runs
        WHERE run_id=$1 AND dispatched_at<=$3 RETURNING owner_token""",
        UUID(str(run_id)),
        token,
        local(now),
    )
    if inserted is None:
        raise ValueError("model guard requires a visible dispatched run")
    return inserted


async def release_model_guard(conn, run_id, owner_token, now):
    """Call only after confirmed model exit; clocks alone never release a guard."""
    return (
        await conn.fetchval(
            """UPDATE research.daily_research_model_guards SET released_at=$3
            WHERE run_id=$1 AND owner_token=$2 AND released_at IS NULL
              AND acquired_at<=$3 RETURNING run_id""",
            UUID(str(run_id)),
            UUID(str(owner_token)),
            local(now),
        )
        is not None
    )
