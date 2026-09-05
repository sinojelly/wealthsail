#!/usr/bin/env bash
# verify_fund_1430_e2e.sh
# 端到端验证「基金 14:30 报告」是否已接入真实数据（估值/申赎/持仓穿透）。
# 需要在【具备数据库与运行环境】的机器上执行；本机（无 Postgres）无法运行。
#
# 用法：
#   export MIGRATION_DATABASE_URL="postgresql://user:pass@host:5432/wealthsail"
#   export WORKER_DATABASE_URL="$MIGRATION_DATABASE_URL"
#   ./tools/verify_fund_1430_e2e.sh
#
# 说明：脚本只做「读」与「触发」，不修改业务数据。

set -euo pipefail

PY="${PY:-.venv/Scripts/python.exe}"
PSQL="${PSQL:-psql}"

echo "====================================================="
echo "[1/5] 运行迁移（会校验边界，含 watchlist / 拆分盘前 / fund_profiles）"
echo "====================================================="
# 先用 dry-run 确认目标；--confirm-target 必须是 host[:port]/database 形式
# （例如 MIGRATION_DATABASE_URL=postgresql://u:p@db.host:5432/wealthsail
#  => --confirm-target db.host:5432/wealthsail）
TARGET=$($PY - <<'PY'
import os
from scripts.migrate import safe_target_name
print(safe_target_name(os.environ["MIGRATION_DATABASE_URL"]))
PY
)
echo "目标标识将是: $TARGET"
$PSQL "$MIGRATION_DATABASE_URL" -tAc "SELECT 1" >/dev/null  # 确认可连接
$PY -m scripts.migrate --environment development --confirm-target "$TARGET"

echo "====================================================="
echo "[2/5] 数据管线前置检查（SQL 诊断）"
echo "====================================================="
# 下面三条查询用于确认 FUND_1430 能拿到真实数据；任一为 0 都说明需要先喂数据。
cat <<'SQL' | $PSQL "$MIGRATION_DATABASE_URL" -f -
-- (a) 持仓穿透快照：054 回填 + ingest_fund_holdings 灌入
SELECT 'fund_lookthrough_rows' AS metric, count(*) AS val
FROM market.fund_lookthrough;

-- (b) 正式净值（天天基金导入）：估值计算的锚
SELECT 'official_nav_rows' AS metric, count(*) AS val
FROM market.market_observations
WHERE field_code='NAV' AND quality_status <> 'UNAVAILABLE';

-- (c) 申赎权威状态（迁移 047）：申赎提醒的真实来源
SELECT 'fund_profiles_rows' AS metric, count(*) AS val
FROM reference.fund_profiles;

-- (d) 已启用 FUND_1430 定时任务与推送偏好（迁移 050 + 043）
SELECT j.job_key, j.enabled,
       (SELECT string_agg(pref||'='||val, ', ')
          FROM jsonb_each_text(j.preferences) AS t(pref,val)) AS prefs
FROM workflow.jobs j
WHERE j.session='FUND_1430';
SQL

echo "====================================================="
echo "[3/5] 启动 / 重启 worker（行情刷新 + 估值计算 + 报告生成）"
echo "====================================================="
echo "请在新终端执行（或先停止旧 worker）："
echo "  $PY -m backend.worker"
echo "（docker-compose 用户：docker compose restart worker）"

echo "====================================================="
echo "[4/5] 触发一次 FUND_1430 重新生成（DEBUG 端点，需 OWNER/ADMIN）"
echo "====================================================="
echo "curl -X POST \"http://localhost:8000/api/v1/debug/reports/trigger/FUND_1430\" \\"
echo "     -H 'Authorization: Bearer <admin_token>'"
echo "（或在前端审计页点击「基金2:30报告」的「生成并推送」按钮）"

echo "====================================================="
echo "[5/5] 验证报告内容（SQL 诊断）"
echo "====================================================="
cat <<'SQL' | $PSQL "$MIGRATION_DATABASE_URL" -f -
-- 最近一次 FUND_1430 报告的摘要与质量
SELECT r.id, r.session, r.generated_at, r.quality, r.status,
       left(r.summary, 120) AS summary_head
FROM reporting.reports r
WHERE r.session='FUND_1430'
ORDER BY r.generated_at DESC
LIMIT 1;

-- 该行报告是否含「估值」明细项（ESTIMATED 说明来自真实估值系统而非暂无）
SELECT count(*) FILTER (WHERE item_type='FUND_ESTIMATE') AS estimate_items,
       count(*) FILTER (WHERE quality='ESTIMATED')        AS estimated_quality
FROM reporting.report_items
WHERE report_id = (
   SELECT id FROM reporting.reports WHERE session='FUND_1430'
   ORDER BY generated_at DESC LIMIT 1
);
SQL

echo "====================================================="
echo "判定："
echo " - 若 (a)(b)(c) 均 > 0 且报告 summary 含具体涨跌幅/申赎状态 => 真实数据已贯通。"
echo " - 若任一为 0 => 先运行天天基金净值导入 + ingest_fund_holdings 回填持仓，再重跑。"
echo "====================================================="
