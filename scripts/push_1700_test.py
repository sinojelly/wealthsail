"""17:00 推送测试：为所有 ACTIVE 企微绑定插入 POST_MARKET outbox 并跟踪投递。

用法: .venv/bin/python scripts/push_1700_test.py
前置：至少一个 ACTIVE wecom 绑定（identity.channel_bindings），Worker 在运行（15s 轮询）。
"""
from __future__ import annotations

import asyncio
import json
import sys

sys.path.insert(0, "/home/lenovo/wealthsail")

import asyncpg

IDEM = "push-1800-test-20260826"


def load_url(key: str) -> str:
    for line in open("/home/lenovo/wealthsail/.env", encoding="utf-8"):
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k == key:
            return v.split("?")[0]
    raise SystemExit(f"missing {key}")


async def main() -> None:
    dsn = load_url("DATABASE_URL").replace(":6543", ":5432")  # 会话池 postgres 角色才有 outbox INSERT
    conn = await asyncio.wait_for(asyncpg.connect(dsn, timeout=30, statement_cache_size=0), timeout=35)
    try:
        # 1. 所有 ACTIVE wecom 绑定（含租户/用户信息）
        bindings = await conn.fetch(
            "SELECT cb.id, cb.tenant_id, cb.user_id, cb.external_username, p.email "
            "FROM identity.channel_bindings cb "
            "JOIN identity.profiles p ON p.id = cb.user_id "
            "WHERE cb.channel_type='wecom' AND cb.status='ACTIVE' "
            "ORDER BY cb.bound_at ASC"
        )
        if not bindings:
            print("FATAL: 没有任何 ACTIVE 企微绑定，无法推送。")
            return
        print(f"共 {len(bindings)} 个 ACTIVE 绑定:")
        for b in bindings:
            print(f"  binding={b['id']} user={b['email']} tenant={b['tenant_id']} wecom={b['external_username']}")

        # 2. 每个绑定插入一条 outbox（幂等）
        for b in bindings:
            n = await conn.fetchval(
                "SELECT count(*) FROM workflow.outbox "
                "WHERE tenant_id=$1 AND topic='REPORT_PUBLISHED' AND idempotency_key=$2",
                b["tenant_id"], f"{IDEM}:{b['id']}",
            )
            if n == 0:
                await conn.execute(
                    """
                    INSERT INTO workflow.outbox
                        (tenant_id, channel_binding_id, topic, aggregate_type, aggregate_id,
                         payload, idempotency_key, status)
                    VALUES ($1, $2, 'REPORT_PUBLISHED', 'reporting.reports',
                            '00000000-0000-0000-0000-000000000098',
                            $3::jsonb, $4, 'PENDING')
                    """,
                    b["tenant_id"], b["id"],
                    json.dumps({
                        "session": "POST_MARKET",
                        "content": "📊 18:00 推送测试：POST_MARKET 盘后推送链路验证（定时 18:00 触发）",
                    }, ensure_ascii=False),
                    f"{IDEM}:{b['id']}",
                )
                print(f"outbox 已插入: {IDEM}:{b['id']}")
            else:
                print(f"outbox 已存在（复用）: {IDEM}:{b['id']}")

        # 3. 跟踪投递（worker 15s 轮询；最多等 90s）
        pending = [(b["tenant_id"], f"{IDEM}:{b['id']}") for b in bindings]
        for _ in range(18):
            await asyncio.sleep(5)
            done = 0
            for tid, key in pending:
                row = await conn.fetchrow(
                    "SELECT status, attempt_count, external_message_id, last_error_class, sent_at "
                    "FROM workflow.outbox WHERE idempotency_key=$1 AND tenant_id=$2", key, tid,
                )
                st = row["status"] if row else "?"
                if st in ("SENT", "DEAD", "UNKNOWN"):
                    done += 1
                    print(f"  终态 {key}: {st} msgid={row['external_message_id']} err={row['last_error_class']}")
                else:
                    print(f"  ...{key}: status={st} attempt={row['attempt_count'] if row else '?'} err={row['last_error_class'] if row else '?'}")
            if done == len(pending):
                return
        print("90s 内未到终态，请查看 worker 日志 /opt/wealthsail/worker.log")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
