"""Give privileged fixture setup an explicit actor without changing API-role tests."""

import json


async def actor_call(conn, operation, tenant, actor, *args, **kwargs):
    # Administrative fixture connections still need the actor proof required by
    # the scoped queue RPC. Never replace claims for a restricted API caller.
    if await conn.fetchval("SELECT current_user=session_user"):
        await conn.execute(
            "SELECT set_config('request.jwt.claims',$1,$2)",
            json.dumps(
                {"sub": str(actor), "tenant_id": str(tenant), "role": "authenticated"}
            ),
            conn.is_in_transaction(),
        )
    return await operation(conn, tenant, actor, *args, **kwargs)
