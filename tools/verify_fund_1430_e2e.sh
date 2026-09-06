#!/usr/bin/env bash
# 验证基金 14:30 真实数据链路：
#   基金持仓 -> 官方净值与持仓披露 -> 成分股行情 -> ESTIMATED_NAV -> 报告。
#
# 用法：
#   export MIGRATION_DATABASE_URL="postgresql://user:pass@host:5432/wealthsail"
#   export API_BASE_URL="http://localhost:8000/api/v1"
#   export ACCESS_TOKEN="<OWNER 或 ADMIN 的 access token>"
#   export FUND_CODES="012733,161726"  # 可选：只读验证真实披露与成分股行情
#   ./tools/verify_fund_1430_e2e.sh
#
# 注意：本脚本会执行数据库迁移检查、提交一次基金估值任务并生成报告，因此会写入
# schema_migrations、workflow.job_runs、market.market_observations 和 reporting 表。
# 它不会插入固定基金状态、默认截止时间或人工编造的成分权重。
set -euo pipefail

PY="${PY:-.venv/bin/python}"
PSQL="${PSQL:-psql}"
API_BASE_URL="${API_BASE_URL:-http://localhost:8000/api/v1}"

if [[ -z "${MIGRATION_DATABASE_URL:-}" ]]; then
  echo "缺少 MIGRATION_DATABASE_URL" >&2
  exit 2
fi

if [[ -z "${ACCESS_TOKEN:-}" ]]; then
  echo "缺少 ACCESS_TOKEN（必须属于当前租户的 OWNER 或 ADMIN）" >&2
  exit 2
fi

echo "[1/6] 校验并执行待应用迁移"
TARGET=$($PY - <<'PY'
import os
from scripts.migrate import safe_target_name

print(safe_target_name(os.environ["MIGRATION_DATABASE_URL"]))
PY
)
echo "数据库目标：$TARGET"
$PSQL "$MIGRATION_DATABASE_URL" -tAc "SELECT 1" >/dev/null
$PY -m scripts.migrate --environment development --confirm-target "$TARGET"

echo "[2/6] 检查真实数据链路的已有事实"
cat <<'SQL' | $PSQL "$MIGRATION_DATABASE_URL" -f -
WITH latest_positions AS (
    SELECT DISTINCT ON (p.tenant_id, p.account_id, p.instrument_id)
           p.tenant_id, p.account_id, p.instrument_id, p.quantity
      FROM ledger.position_snapshots p
     ORDER BY p.tenant_id, p.account_id, p.instrument_id,
              p.as_of DESC, p.created_at DESC
), held_funds AS (
    SELECT p.instrument_id
      FROM latest_positions p
      JOIN reference.instruments i ON i.id = p.instrument_id
     WHERE i.asset_type = 'OFF_EXCHANGE_FUND' AND p.quantity > 0
)
SELECT 'held_otc_funds' AS metric, count(DISTINCT instrument_id) AS value
  FROM held_funds
UNION ALL
SELECT 'real_disclosure_components', count(*)
  FROM market.fund_disclosure_components
UNION ALL
SELECT 'official_nav_observations', count(*)
  FROM market.market_observations
 WHERE field_code = 'NAV' AND quality_status <> 'UNAVAILABLE'
UNION ALL
SELECT 'persisted_estimated_nav', count(*)
  FROM market.market_observations
 WHERE field_code = 'ESTIMATED_NAV' AND quality_status <> 'UNAVAILABLE';

SELECT id, job_name, cron_expr, report_session, is_enabled
  FROM workflow.jobs
 WHERE report_session = 'FUND_1430'
 ORDER BY tenant_id, job_name;
SQL

echo "[3/6] 只读调用真实 Provider（可在非交易时段执行）"
if [[ -n "${FUND_CODES:-}" ]]; then
  FUND_CODES="$FUND_CODES" $PY - <<'PY'
import asyncio
import os

from backend.integrations import market_data


async def main() -> None:
    fund_codes = [code.strip() for code in os.environ["FUND_CODES"].split(",") if code.strip()]
    estimates, disclosures = await asyncio.gather(
        market_data.fetch_fund_intraday_estimates(fund_codes),
        market_data.fetch_fund_disclosures(fund_codes),
    )
    component_codes = sorted({row.component_symbol for row in disclosures})
    quotes = await market_data.fetch_equity_quotes(component_codes)
    print(
        {
            "fundCodes": fund_codes,
            "estimateUsable": sum(
                1
                for item in estimates
                if item.value is not None and item.quality_status != "UNAVAILABLE"
            ),
            "estimates": [
                {
                    "symbol": item.symbol,
                    "value": str(item.value) if item.value is not None else None,
                    "asOf": item.as_of.isoformat(),
                    "quality": item.quality_status,
                    "method": item.method_version,
                }
                for item in estimates
            ],
            "disclosureRows": len(disclosures),
            "componentCodes": len(component_codes),
            "quoteRows": len(quotes),
            "quoteSamples": [
                {
                    "symbol": quote.symbol,
                    "value": str(quote.value),
                    "returnRate": str(quote.return_rate),
                    "asOf": quote.as_of.isoformat(),
                    "quality": quote.quality_status,
                    "method": quote.method_version,
                }
                for quote in quotes[:5]
            ],
        }
    )


asyncio.run(main())
PY
else
  echo "未设置 FUND_CODES，跳过只读 Provider 检查。"
fi

echo "[4/6] 确认 Worker 正在运行"
echo "systemd：sudo systemctl status wealthsail-worker --no-pager"
echo "前台启动：$PY -m backend.worker"

echo "[5/6] 在交易日提交一次真实基金估值任务"
STATUS_RESPONSE=$(curl --fail-with-body --silent --show-error \
  "$API_BASE_URL/operations/fund-estimate" \
  -H "Authorization: Bearer $ACCESS_TOKEN")
CAN_RUN=$(STATUS_RESPONSE="$STATUS_RESPONSE" $PY - <<'PY'
import json
import os

payload = json.loads(os.environ["STATUS_RESPONSE"])
data = payload.get("data", payload)
print("true" if data.get("canRun") else "false")
PY
)
if [[ "$CAN_RUN" == "true" ]]; then
  RUN_RESPONSE=$(curl --fail-with-body --silent --show-error \
    -X POST "$API_BASE_URL/operations/fund-estimate/run" \
    -H "Authorization: Bearer $ACCESS_TOKEN")
  echo "$RUN_RESPONSE"
  echo "任务由 Worker 异步执行；请等待 workflow.job_runs 进入 COMPLETED 后再查看结果。"
else
  echo "当前不是可运行交易日，不提交伪盘中任务；上一步仍可用最近可用行情验证 Provider。"
fi

echo "[6/6] 输出任务、估值和报告回读 SQL"
cat <<'SQL'
-- 1. 最近一次基金估值任务；完成状态应为 COMPLETED。
SELECT id, tenant_id, job_key, status, started_at, finished_at, error_class
  FROM workflow.job_runs
 WHERE job_key LIKE 'manual:FUND_1430:%'
 ORDER BY started_at DESC
 LIMIT 5;

-- 2. 最新持久化基金估值；报告只读取这里的数据，不再读取人工近似权重。
SELECT subject_id AS instrument_id, as_of, value, lower_value, upper_value,
       quality_status, method_version, snapshot_id, metadata
  FROM market.market_observations
 WHERE field_code = 'ESTIMATED_NAV'
 ORDER BY as_of DESC
 LIMIT 30;

-- 3. 最近基金 14:30 报告及证据条目。
SELECT id, tenant_id, report_session, trade_date, version, status, as_of,
       quality_status, left(summary, 160) AS summary_head
  FROM reporting.reports
 WHERE report_session = 'FUND_1430'
 ORDER BY created_at DESC
 LIMIT 5;

SELECT report_id,
       count(*) FILTER (WHERE item_type = 'FUND_ESTIMATE') AS estimate_items,
       count(*) FILTER (WHERE data_quality_status = 'ESTIMATED') AS estimated_items,
       count(*) FILTER (WHERE data_quality_status = 'STALE') AS stale_items
  FROM reporting.report_items
 WHERE report_id = (
     SELECT id
       FROM reporting.reports
      WHERE report_session = 'FUND_1430'
      ORDER BY created_at DESC
      LIMIT 1
 )
 GROUP BY report_id;
SQL

echo "执行上面的回读 SQL：psql \"$MIGRATION_DATABASE_URL\""
echo "非交易时段不提交盘中任务；页面继续使用最近正式净值。若需联调，可直接验证已持久化的最近收盘/净值事实。"
