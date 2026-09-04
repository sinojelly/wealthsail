# 场外基金盘中估值与交易日新鲜度实施计划

> 实施日期：2026-08-30
> 范围：FUND_1430 估值输入、运维手动触发、持仓展示、周末/节假日新鲜度

## 目标与边界

1. 场外基金最近官方净值等于最近交易日时，周末和休市日不得显示为“过期”。
2. FUND_1430 使用基金最新披露持仓中的有效 A 股代码抓取成分涨跌，不要求这些成分预先存在于 WealthSail 标的主数据。
3. OWNER/ADMIN 可以在运维页提交一次持久化的“立即计算基金盘中估值”请求，并查看排队、运行、完成或失败状态；API 本身不得直连行情 Provider。
4. 当前持仓展开区展示盘中估值、估算涨跌、估值区间、成分覆盖率、持仓披露日期、估值时间及弃权原因。
5. 非交易日不允许手动生成伪“盘中”估值；页面解释此时以最近官方净值为准。

## Task 1：修复交易日历缺失时的周末误报

**文件：**

- 修改：`backend/portfolio/repository.py`
- 修改：`backend/market/service.py`
- 修改：`backend/worker.py`
- 测试：`tests/unit/test_portfolio_quote_freshness.py`
- 测试：`tests/unit/test_market_calendar_bootstrap.py`

**步骤：**

1. 先增加失败用例：交易日历为空，但最新官方基金净值日为周五时，当前持仓质量仍应为 FRESH。
2. 抽取统一的“最近有效市场日”SQL：优先交易日历，缺失时回退到已持久化的最新官方 NAV 日期。
3. 在持仓读取与手工重估报价两个入口复用该口径。
4. Worker 启动时若交易日历为空，自动执行一次受控补齐；失败仅告警，不阻断其它循环。
5. 运行相关单元测试。

## Task 2：让基金披露成分无需预建主数据即可参与估值

**文件：**

- 修改：`backend/market/service.py`
- 测试：`tests/unit/test_fund_1430_estimator.py`

**步骤：**

1. 先增加失败用例：披露成分不在 `reference.instruments` 时，仍进入 A 股报价集合。
2. 增加成分代码规范化函数，只接受可识别的六位 A 股/B股所覆盖市场代码；无法识别、港股或其它资产继续降低覆盖率，不静默伪造收益。
3. FUND_1430 抓取规范化后的披露成分。成分观测保留在来源快照中，只有最终基金估值写入规范化基金观测表。
4. 运行估值与行情服务测试。

## Task 3：增加持久化手动触发和状态 API

**文件：**

- 修改：`backend/operations/repository.py`
- 修改：`backend/operations/api.py`
- 修改：`backend/jobs/runner.py`
- 测试：`tests/unit/test_operations_fund_estimate.py`
- 测试：`tests/unit/test_job_runner.py`

**步骤：**

1. 先增加失败用例：OWNER/ADMIN 请求时创建或复用一个非终态 `workflow.job_runs`；并发双击不能创建多个待处理任务。
2. 增加 `GET /operations/fund-estimate`，返回当前交易日、是否可运行、最近手动任务、持有基金估值覆盖情况和最近估值时间。
3. 增加 `POST /operations/fund-estimate/run`，仅 OWNER/ADMIN 且仅交易日可提交；使用事务级 advisory lock 去重。
4. Worker 恢复循环识别 `manual:FUND_1430:*` 任务；取得租约后先刷新 FUND_1430 行情与估值，再生成报告并更新原任务状态。
5. 运行后端 API、Repository、Runner 测试。

## Task 4：把盘中估值元数据加入持仓合同和 UI

**文件：**

- 修改：`backend/portfolio/repository.py`
- 修改：`backend/portfolio/service.py`
- 修改：`frontend/src/api/types.ts`
- 修改：`frontend/src/features/dashboard/components/HoldingsTable.tsx`
- 修改：`frontend/src/index.css`
- 测试：`tests/unit/test_portfolio_integrity.py`
- 测试：`frontend/src/features/dashboard/holdings-table.test.tsx`

**步骤：**

1. 先增加失败用例：持仓 DTO 返回估值值、涨跌、上下界、覆盖率、披露日、估值时间、质量和说明。
2. 持仓查询以 LATERAL JOIN 读取每只场外基金最新 `ESTIMATED_NAV`，不覆盖正式 NAV 的事实语义。
3. 后端使用 Decimal 计算估算涨跌率，安全解析 JSONB 元数据。
4. 前端在基金展开行使用紧凑信息条展示；UNAVAILABLE 时展示可理解的弃权原因，不显示伪数值。
5. 运行后端和前端组件测试。

## Task 5：在运维页提供入口、状态和交易日历诊断

**文件：**

- 修改：`frontend/src/api/operations.ts`
- 修改：`frontend/src/api/hooks.ts`
- 修改：`frontend/src/api/types.ts`
- 修改：`frontend/src/features/operations/OperationsPage.tsx`
- 修改：`frontend/src/index.css`
- 测试：`frontend/src/features/operations/operations-page.test.tsx`

**步骤：**

1. 先增加失败用例：可运行时展示触发按钮；排队/运行中禁用重复提交；非交易日解释无需运行；交易日历缺失显示诊断告警。
2. 接入 GET/POST API 与 5 秒状态轮询，完成后刷新任务、估值和持仓查询缓存。
3. 显示“已估值/持有基金数、最近估值时间、最近运行状态、交易日历状态”。
4. 运行前端单测、类型检查和 lint。

## Task 6：完整验证与交付

1. 运行后端目标测试，再运行完整单元测试。
2. 运行前端测试、类型检查、lint、production build。
3. 运行文档/迁移校验及 `git diff --check`。
4. 复核 `git diff`，确保不覆盖无关文档或用户修改。
5. 使用详细中文提交说明执行 `git commit`，随后 `git push`。
