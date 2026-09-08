# 多源公司行动自动抓取设计

## 目标

在不改变现有研报 API、CSV 人工导入和四时段报告契约的前提下，增加一个仅在 A 股交易日 06:00（`Asia/Shanghai`）运行的 Worker 同步任务。它为所有用户当前持有的 A 股和场内基金抓取分红派息、除权除息、配股及其他可归类公司行动，持久化为可审计的市场事实，供盘前简报读取。

首期不自动抓取场外基金。场外基金公司行动仍通过既有 CSV 导入链路进入系统，不能因为两个股票数据源没有覆盖它而虚构为空或不可用事件。

## 现状与边界

项目已有唯一的公司行动事实表 `market.corporate_actions`：

- migration 057 已为该表提供 `action_date`、`summary`、`source_event_id`、`source_ref` 和 revision 语义；
- `scripts/import_corporate_actions.py` 已使用该表进行 migration-owner 级的 CSV 导入；
- `backend/reports/repository.py` 已从该表读取报告日到未来三天的最新修订，盘前简报已经消费这些事实。

因此本功能禁止新建 `reference.corporate_actions`。新表会造成人工导入、自动抓取和报告读取的双写/双读问题，破坏现有审计模型。

不新增 AI 调用，不向外部数据源传递用户、租户、账户或持仓数量；外部请求只含公开证券代码。

## 数据模型和权限

新增 migration 058，在既有 `market.corporate_actions` 上增加：

- `quality_status TEXT NOT NULL`：使用系统已有质量枚举，自动写入的可展示事实为 `FRESH` 或 `CONFLICT`；
- `verified_by TEXT[] NOT NULL`：记录验证到该事实的来源名称，例如 `{'AKSHARE'}` 或 `{'AKSHARE','BAOSTOCK'}`。

既有 CSV 事实迁移为 `verified_by={'MANUAL_CSV'}`，并保留其原始 `source_id`、`source_ref`、revision 与内容。新 migration 同时登记 `AKSHARE_CORPORATE_ACTIONS` 和 `BAOSTOCK_CORPORATE_ACTIONS` 两个 `reference.data_sources` 记录。

Worker 不获得对 `market.corporate_actions` 的直接 `INSERT`、`UPDATE` 或 `DELETE`。migration-owner 创建一个受控的 `SECURITY DEFINER` 写入函数并仅向 `wealthsail_worker` 授予 `EXECUTE`；函数验证来源、行动类型、事件日期和 revision，并仅接受归一化字段。API 继续只有 `SELECT`。

## 抓取适配层

### 统一接口

新增 `backend/integrations/corporate_actions.py`。模块定义纯数据模型和协调入口：

```text
fetch_corporate_actions(instruments, as_of) -> CorporateActionFetchResult
```

每个归一化事件包含公开代码、市场、资产类型、行动类型、除权日/行动日、登记日、派息日、摘要、参数、来源链接与来源名。结果同时区分：

- `AVAILABLE`：该来源成功响应，可能是空事件集；
- `UNAVAILABLE`：该来源调用、解析或认证失败；
- `UNSUPPORTED`：来源不覆盖该资产类型，不视为接口故障。

两个可用来源都失败且没有一个可用来源响应时，协调器返回 `quality_status='UNAVAILABLE'` 和空事件集。它不会写入伪造的公司行动行；Worker 会写入可观察的任务日志和来源快照结果。正常的空事件集代表“本次没有行动”，与失败严格区分。

### AkShare

AkShare 是首选来源。对 A 股使用固定版本中可用的分红/送配详情接口并读取除权除息日、登记日、派息日、现金/送转参数；对场内基金仅使用 AkShare 确实返回行动日期的接口。接口缺字段、返回空表或异常均明确映射为上述结果，而不是补造日期。

同步实现通过 `asyncio.to_thread` 调用 AkShare，避免阻塞 Worker 的 asyncio 循环；并以有限并发处理证券级请求。

### Baostock

新增 `backend/integrations/baostock_adapter.py`，封装同步 Baostock 会话：单次批处理只登录/退出一次，逐行读取 `query_dividend_data()` 结果。

代码转换规则：

- 以 `6`、`5` 或 `9` 开头的六位代码映射为 `sh.<code>`；
- 以 `0`、`2` 或 `3` 开头的六位代码映射为 `sz.<code>`；
- 其他市场返回 `UNSUPPORTED`，不会猜测交易所。

Baostock 是 A 股分红/除权的交叉验证来源；首期对场内基金返回 `UNSUPPORTED`，不把它计为失败。依赖固定为 `baostock==0.9.3`。

## 合并、质量和幂等

协调器仅在同一持仓标的内按 `symbol + ex_date` 合并事件，满足首期去重契约。多源命中同一键时保留来源并集到 `verified_by`。归一化后的摘要/日期/行动类型一致时质量为 `FRESH`；不能安全合并的差异标为 `CONFLICT` 并保留来源证据，报告不将冲突内容描述为确定事实。

写入函数以稳定的自动来源事件标识匹配既有最新 revision：

- 相同标准化内容不新增 revision；
- 内容、验证来源或质量改变时追加 `revision + 1`；
- 手工 CSV 的 `manual-csv:*` 事件键与自动来源事件键隔离，互不覆盖。

每次任务还记录每个来源的抓取结果快照，保留 `AVAILABLE`、`UNAVAILABLE` 或 `UNSUPPORTED`、抓取时间和不含凭据的错误摘要，供运行审计和排障。

## Worker 调度与数据流

Worker 增加独立的 `CORPORATE_ACTIONS_REFRESH` 维护时段：

- 06:00，`Asia/Shanghai`；
- 使用现有 `reference.trading_calendars` 的 SSE 交易日判断；
- 周末和节假日不执行；
- 与四个报告时段分开，抓取失败不阻断报告生成、市场热点、情绪或 Outbox。

触发时，任务从当前有效持仓中读取去重后的 `CN_STOCK` 与 `EXCHANGE_FUND` 标的及公开交易代码。所有网络调用和解析在数据库事务外完成；仅最终来源快照和受控写入在短事务内落库。

盘前报告继续使用现有 `get_pre_market_corporate_actions()` 查询当天至未来三天的事实。查询扩展返回质量、验证来源与来源名称，仅展示可用且无冲突的事实；因而现有“今日重点关注”和“持仓今日影响”自动获得真实自动导入数据，无需改变报告 API 或前端结构。

## 文件范围

- `migrations/058_corporate_actions_multisource_sync.sql`：字段、数据源、受控写函数、运行审计、最小权限和索引。
- `requirements.txt`：加入固定版本 Baostock。
- `backend/integrations/corporate_actions.py`：统一模型、AkShare 抓取、双源合并和失败语义。
- `backend/integrations/baostock_adapter.py`：代码转换与 Baostock 会话封装。
- `backend/jobs/corporate_actions.py`：持仓读取、来源快照、受控持久化与任务统计。
- `backend/jobs/scheduler.py`、`backend/worker.py`：交易日 06:00 系统维护时段及隔离执行。
- `backend/reports/repository.py`：读取质量和验证来源，仅消费可展示事实。
- `scripts/migrate.py` 与迁移契约测试：验证新增表结构、函数、权限和 readback。
- 新增/扩展单元测试：适配器、合并、持久化、调度和报告读取。

## 失败与降级规则

- AkShare 失败、Baostock 成功：持久化 Baostock 事实，`verified_by={'BAOSTOCK'}`。
- Baostock 失败、AkShare 成功：持久化 AkShare 事实，`verified_by={'AKSHARE'}`。
- 两者成功但均无匹配行动：成功空结果，不生成虚假事件。
- 两个可用来源均失败：返回 `UNAVAILABLE`，记录审计结果，报告保持“暂无已录入公司行动”。
- 任何单标的解析失败只影响该标的；其他持仓仍同步。
- 受控写入失败只标记本次任务失败，不影响已存历史事实或报告读取。

## 测试与验收

实施遵循红—绿测试流程，至少覆盖：

1. `600519 -> sh.600519`、`000001 -> sz.000001` 与未知市场拒绝；
2. Baostock 登录、分页、退出与错误路径；
3. 单源成功、双源成功合并、来源并集、两源失败 `UNAVAILABLE`、成功空结果；
4. `symbol + ex_date` 去重、修订追加和手工/自动事件键隔离；
5. Worker 仅取去重后的 A 股和场内基金、网络调用不在数据库事务中；
6. 06:00 交易日触发，周末/节假日跳过，失败不影响报告调度；
7. 盘前查询只返回可展示的当天至未来三天行动，并携带质量和验证来源；
8. 完整后端测试、迁移发现/readback、前端回归与生产构建。

验收时，人工插入受持仓覆盖的双源测试响应后，应在下一次任务中看到一条 revisioned 公司行动及双来源验证；重新生成盘前简报后，该行动应出现在既有“今日重点关注”和“持仓今日影响”区域。
