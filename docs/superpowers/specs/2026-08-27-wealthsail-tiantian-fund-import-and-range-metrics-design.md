# WealthSail 天天基金持仓导入与区间分析需求设计

> 文档状态：设计已确认，等待实施前最终批准
>
> 设计确认日期：2026-08-27
>
> 适用范围：WealthSail MVP
>
> 关联文档：`docs/01`、`docs/04`、`docs/06`、`docs/08`、`docs/09`、`docs/10`、`docs/11`
>
> 目标迁移：`021_tiantian_fund_import_and_position_metrics.sql`

---

## 1. 文档目的

本文档定义从天天基金本地采集当前持仓和历史基金流水、生成标准 ZIP 数据包、在 WealthSail Web 中预览确认、写入持仓基线、补齐历史净值并展示持仓区间指标的完整需求与设计。

本文档同时作为以下工作的实施基线：

- 本地天天基金采集工具升级；
- Web 导入向导和持仓指标展示；
- 后端导入、账户绑定、幂等确认和指标计算；
- PostgreSQL 数据模型、约束、RLS 和数据库迁移；
- Worker 历史净值回补和每日指标重算；
- 自动化测试、人工验收、部署、监控和失败恢复。

本文档不把导入描述成简单的“上传文件后覆盖持仓”。导入会影响投资账本和后续分析，因此必须明确区分外部来源事实、用户确认、账本影响和派生指标。

---

## 2. 背景与问题

### 2.1 现状

现有本地采集器已经能够：

- 在独立可见 Edge 中由用户完成天天基金登录；
- 分页查询成功交易；
- 识别普通基金交易；
- 排除大部分活期宝现金管理流水；
- 输出 `manifest.json`、交易 JSON/CSV、排除项和待复核项；
- 使用来源记录 ID 和内容哈希支持后续去重。

现有实现仍存在以下缺口：

1. 只采集交易历史，没有当前持仓快照；
2. Web 初始化导入页面只做浏览器本地 CSV 格式预览，没有连接后端批量导入；
3. 后端只支持单只持仓的 DRAFT/CONFIRMED 基线导入；
4. 天天基金历史流水不能证明当前真实持仓，尤其可能缺少更早历史、费用、转换关系和特殊调整；
5. 场外基金行情目前只获取最新单位净值，没有持仓期间的完整历史净值；
6. 持仓接口和页面尚未展示持仓开始日、持有天数、期间高低点及当前位置。

### 2.2 核心风险

如果直接把历史流水转换成 BUY、SELL、DIVIDEND 等真实账本事件，可能因为历史不完整或业务语义误判产生错误成本和错误仓位。错误账本会继续污染收益、风险、报告和建议，风险高于缺少历史明细。

因此，本期采用“当前持仓快照为账本基线，历史流水为来源与分析数据”的方案。

---

## 3. 已批准的关键决策

| 决策项 | 批准结果 | 设计含义 |
|---|---|---|
| 账本口径 | 当前持仓快照作为权威基线 | 历史流水不直接生成真实买卖事件 |
| 导入载体 | 单个 ZIP + 预览确认 | 不让服务器读取用户本地目录 |
| 账户映射 | 一个天天基金账号绑定一个 WealthSail 投资账户 | 支持重复增量导入和多账号扩展 |
| 当前价格 | 最新已公布单位净值 | 不使用盘中估值计算高低点 |
| 持仓起点 | 最近一次从零仓位转为正仓位 | 表达本轮连续持仓周期 |
| 高低点区间 | 本轮持仓开始日至最新净值日 | 与当前仍持有的这轮投资对应 |
| 重复导入 | 来源流水去重、当前持仓逐基金替换基线 | 不累加重复持仓，不用不可靠差额成本 |
| 净值补齐 | 后端异步回补并缓存 | 导入确认不等待外部行情网络请求 |
| UI | 登录后提供“导入 / 更新持仓” | 支持首次和后续重复导入 |
| 安全 | 用户本地登录，凭据不上传 | WealthSail 不保存天天基金账号密码、Cookie 或 Token |

### 3.1 备选方案及未采用原因

#### 方案一：本地采集、ZIP 上传、服务端统一解析（采用）

优点是凭据隔离、来源可追溯、算法可版本化重算、容易支持多用户和其他平台。代价是后端需要实现完整预览和确认流程。

#### 方案二：本地采集器计算最终持仓和指标

后端较简单，但业务规则会分散在客户端工具中。采集器版本变化后，历史结果难以统一重算，不采用。

#### 方案三：WealthSail 服务端直接登录天天基金

表面操作少，但需要处理账号凭据、验证码、反爬和会话安全，不符合当前安全边界，不采用。

#### 方案四：用全部历史流水重建真实账本

理论上可做完整收益归因，但天天基金历史可能缺少费用、确认信息、转换对手方及更早数据。当前无法保证结果可信，不纳入 MVP。

---

## 4. 目标、价值和非目标

### 4.1 业务目标

- 用户只需完成一次本地登录采集，并在 Web 中选择一个 ZIP；
- 首次导入后，WealthSail 持仓与天天基金当前持仓一致；
- 后续重复导入不会重复增加份额或重复写入历史流水；
- 用户能够看到本轮持仓多久、期间最高/最低净值及当前所处位置；
- 活期宝现金管理明细不会淹没基金投资记录；
- 导入结果能够被审计、撤销旧影响、重新计算和安全扩展。

### 4.2 用户价值

- 减少逐只手工录入基金份额、成本和购买日期的工作；
- 以当前持仓快照校正资产事实，避免历史缺失导致仓位错误；
- 用持仓周期高低点辅助判断回撤、反弹幅度和位置，不只看当日涨跌；
- 保存历史来源记录，为后续现金流分析、行为复盘和收益归因打基础；
- 通过明确的数据质量状态，避免把不完整历史伪装成精确结论。

### 4.3 本期非目标

- 服务端保存天天基金登录凭据；
- 服务端无人值守登录或绕过验证码；
- 自动定时抓取用户的天天基金账户；
- 导入活期宝现金账户、充值、取现和每日收益；
- 把历史流水重建成真实 BUY/SELL 账本；
- 分红复权或累计净值总回报高低点；
- 其他基金销售平台导入；
- 微博、抖音等研究资料采集。

---

## 5. 用户角色与权限

| 角色 | 上传/预览 | 确认/取消 | 指标重试 | 查看持仓和历史 |
|---|---:|---:|---:|---:|
| OWNER | 是 | 是 | 是 | 是 |
| ADMIN | 是 | 是 | 是 | 是 |
| EDITOR | 是 | 是 | 是 | 是 |
| VIEWER | 否 | 否 | 否 | 是 |

所有业务接口从已认证会话取得 `tenant_id` 和用户角色。请求 Body 不接受客户端自报租户。RLS 是第二道防线，不能替代应用层授权。

---

## 6. 端到端用户流程

```text
用户启动本地采集器
        ↓
独立 Edge 中自行扫码/验证码登录
        ↓
采集当前持仓 + 历史成功基金流水
        ↓
分类普通基金、待确认项和活期宝排除项
        ↓
生成 v2 ZIP、清单和 SHA-256
        ↓
登录 WealthSail，点击“导入 / 更新持仓”
        ↓
选择或创建目标账户，上传单个 ZIP
        ↓
后端安全校验、主数据解析、去重和冲突比较
        ↓
页面展示持仓、历史摘要、问题和排除项
        ↓
用户补充必要信息并明确确认
        ↓
事务内确认来源事实并逐基金替换持仓基线
        ↓
持仓立即可见，净值回补任务异步执行
        ↓
页面展示持有天数、高低点和区间位置
```

浏览器不会把本地目录路径交给服务器。文件选择器只向用户明确授权的单个 ZIP 读取字节。

---

## 7. 功能需求

### 7.1 本地采集器

| 编号 | 需求 |
|---|---|
| TT-FR-001 | 采集器必须使用独立可见浏览器，由用户自行完成登录和验证码。 |
| TT-FR-002 | 采集器必须自动采集当前基金持仓快照。 |
| TT-FR-003 | 采集器必须自动分页采集指定区间内的成功基金交易。 |
| TT-FR-004 | 采集器必须区分真实基金业务和活期宝现金管理业务。 |
| TT-FR-005 | 采集器必须输出结构化 JSON、人工复核报告及标准 ZIP。 |
| TT-FR-006 | 采集器必须校验页面汇总、持仓条数、历史分页完整性和数值合法性。 |
| TT-FR-007 | 采集器不得把密码、Cookie、Token 或完整账户号写入输出。 |
| TT-FR-008 | 同一采集配置必须维持稳定的匿名来源账户标识。 |
| TT-FR-009 | 组合一键买入、卖出和调仓必须保留为待复核来源事实，不得因状态文案缺失而直接丢弃，也不得伪造成逐基金成交。 |

### 7.2 Web 导入

| 编号 | 需求 |
|---|---|
| TT-FR-020 | 登录后的有权限用户必须能从资产看板进入“导入 / 更新持仓”。 |
| TT-FR-021 | 用户必须能够选择已有投资账户或创建天天基金账户。 |
| TT-FR-022 | Web 必须支持选择或拖放一个 `.zip` 文件。 |
| TT-FR-023 | Web 必须显示上传和校验进度，并允许在提交前取消。 |
| TT-FR-024 | 预览必须显示新增、变化、不变、清仓、冲突和待确认数量。 |
| TT-FR-025 | 预览必须分开展示当前持仓、历史摘要、问题与排除项。 |
| TT-FR-026 | 阻断问题未解决时，确认按钮必须禁用并说明原因。 |
| TT-FR-027 | 确认前必须要求用户明确接受快照基线语义。 |
| TT-FR-028 | 页面刷新后必须能按批次 ID 恢复未完成预览。 |
| TT-FR-029 | 必须提供最近导入记录和每批状态。 |

### 7.3 账本与来源数据

| 编号 | 需求 |
|---|---|
| TT-FR-040 | 当前持仓快照必须作为来源账户的权威基线。 |
| TT-FR-041 | 历史交易只作为来源和分析数据，不自动生成真实买卖事件。 |
| TT-FR-042 | 正式来源流水必须按来源账号和外部流水 ID 去重。 |
| TT-FR-043 | 相同 ZIP 重放不得产生第二次账本影响。 |
| TT-FR-044 | 后续快照必须逐基金比较并只替换发生变化的来源基线。 |
| TT-FR-045 | 替换基线必须使用 REVERSAL + 新 ADJUSTMENT，而不是覆盖旧事件。 |
| TT-FR-046 | 清仓必须撤销旧基线且不留下零数量持仓。 |
| TT-FR-047 | 目标账户存在其他来源或手工真实账本事实时，系统不得自动覆盖。 |
| TT-FR-048 | 预览和确认之间目标账户事实变化时，确认必须失败并要求重新预览。 |

### 7.4 行情与指标

| 编号 | 需求 |
|---|---|
| TT-FR-060 | 系统必须按证据优先级推导本轮连续持仓开始日，并说明推导方法、可信度与结构化原因。 |
| TT-FR-061 | 系统必须按自然日计算持仓天数。 |
| TT-FR-062 | 系统必须回补持仓开始日至今的官方单位净值。 |
| TT-FR-063 | 系统必须计算期间最高/最低单位净值及日期。 |
| TT-FR-064 | 系统必须计算距最高点、距最低点和区间位置。 |
| TT-FR-065 | 历史不完整、净值缺失或来源冲突时必须返回质量状态。 |
| TT-FR-066 | 行情回补失败不得回滚已经确认的持仓。 |
| TT-FR-067 | 每日最新净值更新后必须重算仍持有基金的指标。 |
| TT-FR-068 | 多用户持有同一基金时必须共享历史净值缓存。 |
| TT-FR-069 | 有候选日期时 Web 必须自动预填；用户不修改即采用候选日期，用户修改后标记为高可信用户确认。 |
| TT-FR-070 | 缺少逐基金日期时，系统必须使用同一脱敏来源账户的组合买入或调仓活动给出可编辑候选，不得跨账户猜测。 |

---

## 8. 非功能需求

### 8.1 正确性

- 金融数值在数据库和 Python 中使用 Decimal/NUMERIC；
- API 中金额、份额、净值和百分比使用十进制字符串；
- 前端不得使用二进制浮点数进行金融计算；
- 当前持仓、来源流水、账本事件和派生指标必须有清晰边界；
- 未知值返回 `null` 和质量状态，不能用零代替。

### 8.2 性能边界

- ZIP 压缩后不超过 20 MB；
- 解压后总大小不超过 100 MB；
- ZIP 最多 20 个文件；
- 来源流水最多 20,000 条；
- 当前持仓最多 1,000 项；
- 典型 50 项持仓、2,000 条流水预览目标小于 5 秒；
- 典型确认事务目标小于 3 秒；
- 持仓列表目标小于 500 毫秒；
- 历史净值回补不得占用上传或确认 HTTP 请求。

### 8.3 可扩展性

- 来源绑定、来源流水和导入批次使用通用 provider 字段；
- ZIP Manifest 带 Schema 版本；
- 解析器、持仓起点算法和指标均带方法版本；
- 历史净值继续进入通用 `market.market_observations`；
- 未来可新增其他销售平台解析器，而不重写账本确认流程。

---

## 9. 本地采集器设计

### 9.1 采集步骤

1. 启动受工具控制的独立 Edge 用户数据目录；
2. 打开天天基金登录或持仓页；
3. 等待用户完成扫码、验证码和登录；
4. 验证持仓页已进入登录态；
5. 优先捕获当前持仓页使用的结构化接口；
6. 结构化接口不可用时，使用受测试覆盖的 DOM 提取作为降级；
7. 分页抓取历史成功交易；
8. 标准化、分类、校验并生成报告；
9. 生成上传 ZIP；
10. 打印结果摘要和 ZIP 绝对路径。

采集器不自动绕过验证码，不注入用户主浏览器，不上传浏览器会话数据。

### 9.2 当前持仓字段

- `productCode`
- `productName`
- `quantity`
- `availableQuantity`
- `averageCost`
- `costAmount`
- `marketValue`
- `holdingProfit`
- `holdingProfitRate`
- `latestNav`
- `latestNavDate`
- `accountMask`
- `sourceRecordId`
- `capturedAt`

来源不直接提供平均成本时，可以由可信的成本总额除以份额推导，并记录推导方法。成本总额也无法可靠取得时，该持仓进入阻断性校验，不得默认为零。

### 9.3 历史业务分类

标准业务类型：

- `PURCHASE`
- `REDEEM`
- `CONVERSION_IN`
- `CONVERSION_OUT`
- `DIVIDEND_CASH`
- `DIVIDEND_REINVEST`
- `FORCED_ADJUSTMENT`
- `NON_POSITION`
- `UNKNOWN`

关键规则：

- “活期宝转入”若实际表示使用活期宝资金购买某只六位代码基金，应识别为基金买入；
- 活期宝充值、普通取现、快速取现、每日收益和信用卡还款属于现金管理，不进入基金来源流水；
- 真实六位代码货币基金不能因为基金类型是货币型而被排除；
- 基金转换尽量按 traceNo/详情引用配对为转出和转入；
- 缺少一侧的转换、强行调增、活动发放和未知业务进入待确认。

### 9.4 一致性校验

采集器必须检查：

- 当前持仓接口声明总数与实际条数；
- 历史交易总页数、页码连续性和记录数量；
- 基金代码、日期、份额、金额和净值格式；
- 当前明细市值合计与页面汇总差异；
- 标准化流水模拟期末份额与当前持仓份额差异；
- 是否出现重复来源 ID 或同 ID 不同内容。

当前持仓采集不完整属于阻断错误。历史不完整可以输出 ZIP，但必须标记 `PARTIAL_HISTORY`。

### 9.5 私有输出结构

```text
tools/tiantian-fund-importer/private-data/
└── run-YYYYMMDDTHHMMSSZ/
    ├── raw/
    ├── normalized/
    ├── reports/
    └── wealthsail-tiantian-YYYYMMDD.zip
```

`private-data` 保持 Git 忽略。真实账户、资产和浏览器会话数据不得进入测试 fixtures 或提交历史。

---

## 10. ZIP 数据包规范

### 10.1 Schema 标识

```text
wealthsail-tiantian-import/v2
```

### 10.2 允许文件

```text
manifest.json
current-positions.json
fund-transactions.json
review-required.json
excluded-summary.json
```

未声明文件、重复文件和嵌套压缩包均拒绝。活期宝详细流水只保留在本地报告中，上传包只带分类汇总。

### 10.3 Manifest

Manifest 至少包含：

```json
{
  "schemaVersion": "wealthsail-tiantian-import/v2",
  "collectorVersion": "...",
  "provider": "TIANTIAN_FUND",
  "externalAccountKey": "random-anonymous-uuid",
  "sourceWindowStart": "YYYY-MM-DD",
  "sourceWindowEnd": "YYYY-MM-DD",
  "positionsAsOf": "ISO-8601",
  "generatedAt": "ISO-8601",
  "completeness": "COMPLETE|PARTIAL",
  "counts": {},
  "files": [
    {"name": "current-positions.json", "sha256": "...", "records": 0}
  ]
}
```

匿名来源账号 UUID 由本地私有配置生成并稳定保存，不从用户名、手机号、身份证或银行卡号直接派生。私有配置丢失时，Web 可要求用户明确重新绑定，不能静默认为同一账号。

### 10.4 ZIP 安全约束

后端必须拒绝：

- 绝对路径；
- `..` 路径穿越，包括反斜杠变体；
- 符号链接；
- 加密 ZIP；
- 嵌套压缩文件；
- 超过文件数、压缩大小、解压大小或压缩率上限；
- Manifest 未声明或哈希不一致的文件；
- 畸形 UTF-8、超深 JSON、超长字段和超大数组。

ZIP 以流式方式写入受控临时文件。成功、失败和客户端取消后都必须清理。

---

## 11. 数据模型设计

### 11.1 设计原则

1. 复用现有 `ledger.portfolio_import_batches/items`；
2. 来源事实和账本影响分开；
3. 预览暂存数据和正式来源数据分开；
4. 行情事实和持仓派生指标分开；
5. 手工确认值必须可追溯，不能写进不可解释的 JSON 黑盒；
6. 所有租户表具有同租户外键、RLS、最小授权和必要索引。

### 11.2 `ledger.portfolio_source_bindings`

用途：绑定匿名外部来源账号和 WealthSail 账户。

主要字段：

- `id`
- `tenant_id`
- `account_id`
- `provider_code`
- `external_account_key`
- `display_name`
- `status`：`PENDING / ACTIVE / DISABLED`
- `created_by_batch_id`
- `first_imported_at`
- `last_imported_at`
- `created_at`
- `updated_at`

唯一约束：

```text
(tenant_id, provider_code, external_account_key)
```

新账号首次预览可创建 PENDING 绑定；确认后转 ACTIVE；取消或过期的 PENDING 绑定可以安全删除。ACTIVE 绑定不得直接改绑账户，必须通过显式管理命令和审计。

### 11.3 扩展 `ledger.portfolio_import_batches`

`source_type` 增加 `TIANTIAN_FUND_ZIP`，并增加：

- `source_binding_id`
- `package_schema_version`
- `package_content_hash`
- `source_as_of`
- `parser_version`
- `preview_summary`
- `validation_status`
- `validation_errors`
- `target_account_state_hash`

批次继续使用现有主状态：

```text
DRAFT → CONFIRMED
DRAFT → CANCELLED
```

不额外引入 `UPLOADED/PREVIEWED` 主状态。只有完整解析并形成可恢复预览后才创建 DRAFT；上传或解析失败直接返回明确错误。指标回补状态属于后台任务和指标投影，不混入导入批次主状态。

### 11.4 扩展 `ledger.portfolio_import_items`

现有 `CASH_BALANCE/POSITION` 保持兼容，增加 `POSITION_SNAPSHOT`，用于表达来源快照中的绝对目标状态。

增加字段：

- `external_record_id`
- `source_symbol`
- `source_market_value`
- `source_nav`
- `source_nav_date`
- `comparison_status`：`NEW / UNCHANGED / CHANGED / CLOSED / CONFLICT`
- `candidate_holding_started_on`
- `holding_start_status`
- `holding_start_method`
- `holding_start_confidence`
- `holding_start_reasons`
- `user_holding_started_on`
- `source_payload`

`POSITION_SNAPSHOT` 允许数量为零。零数量项目只用于表达已清仓基金并撤销旧来源基线，不产生新的正向 ADJUSTMENT。

每个已确认 ZIP 批次保留一份完整当前快照项目。后续指标重算读取最近确认批次中的用户补录日期，从而不需要把人工事实塞进派生指标表。

### 11.5 `ledger.portfolio_import_source_records`

用途：保存 DRAFT 预览阶段的历史来源记录和分类结果。

主要字段：

- `tenant_id`
- `batch_id`
- `record_type`
- `external_record_id`
- `instrument_id`
- `occurred_at`
- `classification`
- `normalized_payload`
- `content_hash`
- `validation_status`
- `validation_errors`
- `created_at`

唯一约束：

```text
(tenant_id, batch_id, record_type, external_record_id)
```

取消或过期 DRAFT 后可清理这些暂存记录。它们不是正式来源事实。

### 11.6 `ledger.portfolio_source_transactions`

用途：保存用户确认后的外部基金历史流水。

主要字段：

- `tenant_id`
- `source_binding_id`
- `source_import_batch_id`
- `external_transaction_id`
- `instrument_id`
- `product_code`
- `product_name`
- `occurred_at`
- `business_code`
- `business_type`
- `application_value`
- `application_unit`
- `confirmation_value`
- `confirmation_unit`
- `classification`
- `source_payload`
- `content_hash`
- `created_at`

唯一约束：

```text
(tenant_id, source_binding_id, external_transaction_id)
```

相同外部 ID 且内容哈希相同视为重复；相同 ID 但内容不同视为来源冲突，不能静默覆盖。

### 11.7 `ledger.position_period_metrics`

用途：保存每日持仓区间指标派生投影，避免持仓列表每次扫描多年净值。

主要字段：

- `tenant_id`
- `account_id`
- `instrument_id`
- `source_binding_id`
- `as_of`
- `holding_started_on`
- `holding_days`
- `holding_start_method`
- `holding_start_confidence`
- `current_nav`
- `current_nav_date`
- `period_high_nav`
- `period_high_date`
- `period_low_nav`
- `period_low_date`
- `from_high_pct`
- `from_low_pct`
- `range_position_pct`
- `calculation_status`
- `quality_reasons`
- `calculation_version`
- `updated_at`

唯一约束：

```text
(tenant_id, account_id, instrument_id, as_of)
```

持仓成本收益率继续由 `position_snapshots` 计算，不在本表重复存储。

### 11.8 行情数据

历史单位净值继续存入：

```text
market.market_observations
```

使用 `field_code = NAV`，保留 source、snapshot、as_of、quality、method_version 和 revision。不得另建天天基金专用净值表。

### 11.9 后台任务

扩展现有 `workflow.job_runs`：

- `job_type`
- `payload`
- `available_at`

新增任务类型：

- `FUND_NAV_BACKFILL`
- `POSITION_PERIOD_METRICS_REBUILD`

复用现有租约、重试和运行状态机制，不使用进程内 `asyncio.create_task` 承担不可丢失任务。

---

## 12. 账本确认语义

### 12.1 首次导入

对于每个正持仓 `POSITION_SNAPSHOT` 项目：

- 创建一个 `ADJUSTMENT`；
- 只创建 INSTRUMENT leg；
- quantity 为当前来源份额；
- unit_price 为来源平均成本；
- 不创建虚构现金流；
- 通过 import batch/item 建立来源引用。

该事件表示“用户确认的外部账户持仓基线”，不声称在 WealthSail 中发生过真实买入。

### 12.2 后续导入

逐基金处理：

- `NEW`：创建新 ADJUSTMENT；
- `UNCHANGED`：不产生账本事件；
- `CHANGED`：为该基金上一个仍生效的来源 ADJUSTMENT 创建 REVERSAL，再创建新 ADJUSTMENT；
- `CLOSED`：只创建 REVERSAL；
- `CONFLICT`：阻止确认。

采用逐基金替换而不是每次撤销整个账户，避免无变化持仓产生无意义事件，同时能精确重置变化基金的份额和平均成本。

### 12.3 无持仓变化但有新历史流水

允许确认。正式来源流水可以增加，而账本不产生任何事件。数据库触发器不能继续假设“每个确认批次的每个项目都必须产生 ADJUSTMENT”，而应只对需要账本影响的项目检查对应事件。

### 12.4 空快照

- 已绑定账户存在来源基线时，为每只现有基金生成 CLOSED 项目；
- 页面进行二次确认；
- 确认后逐只创建 REVERSAL；
- 新账号首次导入空快照没有投资意义，应阻止确认并提示没有可导入持仓。

### 12.5 冲突保护

自动基线替换要求目标账户是该来源的专用账户。发现以下事实时阻止确认：

- 同一 instrument 存在非该 binding 产生的有效账本事件；
- ACTIVE binding 指向另一个目标账户；
- 预览后的目标账户事实哈希变化；
- 当前快照时间早于最近确认快照；
- 同一 binding 正在被另一个事务确认。

`target_account_state_hash` 由当前有效账本事件的稳定身份集合计算。确认时重新计算，避免依赖不存在的全局自增游标。

### 12.6 原子性与幂等

确认事务按以下顺序执行：

1. 锁定批次和来源绑定；
2. 校验状态、版本、权限和账户事实哈希；
3. 将合格暂存流水插入正式来源表；
4. 创建必要的 REVERSAL 和 ADJUSTMENT；
5. 更新批次和绑定；
6. 重建当前持仓和账户净值投影；
7. 写审计日志；
8. 写入持久化后台任务；
9. 提交。

任一步失败全部回滚。相同幂等键和相同内容返回原结果；相同幂等键和不同内容返回冲突。

---

## 13. 持仓开始日算法

### 13.1 份额变化映射

| 业务 | 份额影响 |
|---|---:|
| PURCHASE | 增加确认份额 |
| REDEEM | 减少确认份额 |
| CONVERSION_IN | 增加转入确认份额 |
| CONVERSION_OUT | 减少转出确认份额 |
| DIVIDEND_REINVEST | 增加确认份额 |
| DIVIDEND_CASH | 无份额变化 |
| FORCED_ADJUSTMENT / UNKNOWN | 不自动参与，进入待确认 |

### 13.2 推导规则

1. 按确认时间和稳定来源 ID 排序；
2. 用 Decimal 逐笔累计份额；
3. 容差为 `max(0.01, 当前份额 × 0.000001)`；
4. 找到最近一次累计份额从不大于容差变为大于容差的日期；
5. 将模拟期末份额与当前快照比较；
6. 输出候选开始日、状态、原因和算法版本。

逐基金份额流水是最高优先级证据。`RECONCILED` 输出
`TRANSACTION_RECONCILED / HIGH`；逐基金历史不完整但已有候选时输出 `PARTIAL_HISTORY / LOW`，
并继续优先于任何组合级记录。只有逐基金结果为 `INSUFFICIENT_DATA` 时才允许组合回退：

1. 只选择与当前持仓 `accountMask` 后四位一致、发生时间不晚于快照的组合活动；
2. 有组合买入时取最早组合买入日；没有买入但有调仓时取最早调仓日；
3. 纯组合买入输出 `PORTFOLIO_ACTIVITY_ESTIMATE / MEDIUM`；存在调仓或只有调仓时输出
   `PORTFOLIO_ACTIVITY_ESTIMATE / LOW`；
4. 保存“同账户匹配”“缺少组合成分明细”“存在调仓”“仅有调仓”等结构化原因；
5. 组合活动只产生日期候选，不生成逐基金交易、金额或份额。

### 13.3 状态

- `RECONCILED`：期末份额在容差内一致；
- `PARTIAL_HISTORY`：最早流水时已经持仓，真实起点可能更早；
- `MISMATCH`：期末模拟份额与当前快照不一致；
- `INSUFFICIENT_DATA`：缺少关键份额或转换记录；
- `USER_CONFIRMED`：用户明确补充开始日。

预览页把候选日期自动放入日期控件，但未编辑的候选不冒充用户决策。确认后，后台采用候选日期及其
原始方法/可信度；用户实际修改日期时才写 `user_holding_started_on`，后续指标方法变为
`USER_CONFIRMED / HIGH`。用户可以修改低可信组合候选，也可以保留默认值。

同日清仓并重新买入但缺少精确时刻时，可把开始日确定为当天，状态降级并在原因中说明。

### 13.4 持仓天数

```text
holding_days = as_of - holding_started_on + 1
```

使用自然日，不使用交易日。开始日未知时返回 `null`。

---

## 14. 净值和区间指标算法

### 14.1 数据口径

- 当前价格为最近已公布的官方单位净值；
- 不使用盘中估值计算区间高低点；
- 区间为本轮持仓开始日至最新官方净值日期；
- 持仓开始日不是公布日时，从之后第一条有效净值开始；
- 周末和法定节假日缺少净值不是数据缺口。

### 14.2 公式

```text
period_high_nav = MAX(NAV)
period_low_nav  = MIN(NAV)

from_high_pct = (current_nav - period_high_nav)
                / period_high_nav × 100

from_low_pct  = (current_nav - period_low_nav)
                / period_low_nav × 100

range_position_pct = (current_nav - period_low_nav)
                     / (period_high_nav - period_low_nav) × 100
```

规则：

- 当前净值包含在区间中；
- 高低点多次出现时记录最近一次日期；
- 高低相等时 `range_position_pct = null`，页面显示“区间尚未形成”；
- 最低净值为零或异常值时不执行除法；
- 所有比例由后端 Decimal 计算；
- 正常完整数据下区间位置应在 0% 到 100%，超界表示数据或计算异常。

### 14.3 分红解释

现金分红会造成单位净值自然下降，因此单位净值高低点不等于包含分红的总回报。UI 必须展示：

> 净值区间反映单位净值高低，不等同于包含分红的总收益。

持仓成本收益率独立展示。本期不把累计净值或复权净值与单位净值混算。

### 14.4 质量状态

- `READY`：持仓起点和区间净值完整；
- `PENDING`：净值回补或计算尚未完成；
- `PARTIAL`：历史起点或区间净值不完整；
- `CONFLICT`：来源流水、账户事实或行情来源存在冲突。

质量原因使用结构化代码数组保存。UI 展示可理解中文，不直接显示内部异常堆栈。

---

## 15. 后端接口设计

基础路径：`/api/v1`。

### 15.1 上传预览

```http
POST /portfolio/imports/tiantian/preview
Content-Type: multipart/form-data
Idempotency-Key: <required>
```

请求包含 ZIP、已有 `accountId` 或待创建账户名称。二者互斥。

响应包含：

- `batchId`
- `version`
- `package`
- `sourceBinding`
- `summary`
- `positions`
- `historySummary`
- `warnings`
- `blockingErrors`
- `canConfirm`

预览不创建账本事件，不启动外部行情请求。

### 15.2 查询批次

```http
GET /portfolio/imports/{batchId}
GET /portfolio/imports?sourceType=TIANTIAN_FUND_ZIP&cursor=...
```

用于页面刷新恢复和导入历史。列表遵循 `items + nextCursor`。

### 15.3 确认

```http
POST /portfolio/imports/{batchId}/confirm
Idempotency-Key: <required>
If-Match: "<version>"
```

请求只携带允许的人工决策，例如用户确认的持仓开始日和明确排除的待确认历史记录。服务器重新读取批次，不信任客户端回传的份额、成本或比较状态。

响应示例：

```json
{
  "batchId": "...",
  "status": "CONFIRMED",
  "version": 2,
  "metricsStatus": "PENDING",
  "positionCount": 45,
  "historyImported": 296,
  "historyDuplicated": 0,
  "projectionCursor": "..."
}
```

### 15.4 取消和指标重试

```http
POST /portfolio/imports/{batchId}/cancel
Idempotency-Key: <required>
If-Match: "<version>"

POST /portfolio/imports/{batchId}/metrics/retry
Idempotency-Key: <required>
```

只有 DRAFT 可以取消。已确认批次不能删除。指标重试不重复产生账本影响。

### 15.5 持仓接口扩展

```http
GET /portfolio/positions
```

每项增加：

```json
{
  "holdingPeriod": {
    "startedOn": "2024-03-15",
    "days": 895,
    "method": "TRANSACTION_RECONCILED"
  },
  "rangeMetrics": {
    "high": "1.5000",
    "highDate": "2025-10-08",
    "low": "1.0000",
    "lowDate": "2024-04-23",
    "fromHighPct": "-20.00",
    "fromLowPct": "20.00",
    "rangePositionPct": "40.00",
    "status": "READY",
    "qualityReasons": [],
    "asOf": "2026-08-26"
  }
}
```

缺失值返回 `null`。不得将 `PENDING` 或 `PARTIAL` 数据伪装成零。

### 15.6 错误码

- `IMPORT_PACKAGE_TOO_LARGE`
- `IMPORT_PACKAGE_INVALID`
- `IMPORT_SCHEMA_UNSUPPORTED`
- `IMPORT_HASH_MISMATCH`
- `IMPORT_SOURCE_ACCOUNT_CONFLICT`
- `IMPORT_PREVIEW_STALE`
- `IMPORT_NOT_CONFIRMABLE`
- `IMPORT_IDEMPOTENCY_CONFLICT`
- `FUND_INSTRUMENT_UNRESOLVED`
- `POSITION_COST_UNAVAILABLE`

错误继续使用项目统一 envelope，并提供 `requestId` 和可操作的 `fieldErrors`。

---

## 16. 后端代码组织

功能保留在现有 portfolio 领域内：

```text
backend/portfolio/
├── api.py
├── service.py
├── repository.py
└── imports/
    ├── package.py
    ├── tiantian.py
    ├── service.py
    └── repository.py
```

- `package.py`：纯 ZIP、Manifest、哈希和安全校验；
- `tiantian.py`：天天基金 v2 DTO、标准化和分类；
- `service.py`：预览、确认、取消、幂等和任务编排；
- `repository.py`：导入批次、来源绑定、来源记录和账本写入。

不新增顶层微服务，不把每个小概念拆成单独目录。API 路由不直接写 SQL，解析器不访问数据库，仓储不包含 UI 文案。

---

## 17. 前端设计

### 17.1 入口

资产看板的“初始化导入”改为“导入 / 更新持仓”。入口位于：

- 资产看板页头；
- 持仓表右上角。

复用 `/onboarding`，页面名称改为“持仓导入与更新”。保留手工添加和通用 CSV 正确内容，本期完整接通天天基金 ZIP。

### 17.2 组件

```text
frontend/src/features/onboarding/
├── OnboardingPage.tsx
└── components/
    ├── ImportSourceSelector.tsx
    ├── TiantianImportWizard.tsx
    ├── ImportPackageStep.tsx
    ├── ImportPreviewStep.tsx
    ├── ImportConfirmStep.tsx
    ├── ImportResultStep.tsx
    └── ImportHistory.tsx

frontend/src/features/dashboard/components/
├── HoldingsTable.tsx
└── FundRangeMetrics.tsx
```

TanStack Query 保存服务端状态；ZIP 和步骤草稿只在 feature scope。金融 DTO 保持十进制字符串。

### 17.3 导入向导

#### 步骤一：账户和文件

- 选择已有账户或创建天天基金账户；
- 选择/拖放 ZIP；
- 显示支持版本和大小限制；
- 显示上传进度、校验阶段和取消入口；
- 提供本地采集器使用说明。

#### 步骤二：预览

汇总显示：

- 持仓总数；
- 新增、变化、不变、清仓、冲突；
- 新增和重复历史流水；
- 排除活期宝数量；
- 待确认数量；
- 数据起止日期。

三个页签：

1. 当前持仓；
2. 历史流水摘要；
3. 问题与排除项。

状态必须同时有文字和图标，不能只用颜色。

#### 步骤三：异常处理

非阻断问题允许：

- 接受开始日未知；
- 手工补充开始日；
- 采用系统候选日期；
- 明确排除异常历史记录。

阻断问题未解决时确认按钮禁用。

#### 步骤四：确认

页面明确说明：

- 当前持仓快照更新来源账户基线；
- 历史流水不生成真实成交；
- 活期宝明细不导入；
- 变化基线会以可审计撤销事件替换；
- 行情指标可能后台继续计算。

用户必须勾选：

> 我已核对当前持仓，并确认使用该快照更新天天基金账户。

#### 步骤五：结果

区分：

- 持仓和指标均完成；
- 持仓成功、指标计算中；
- 持仓成功、指标暂时失败；
- 确认事务失败、没有写入。

页面可以轮询指标任务，离开页面不影响 Worker。

### 17.4 持仓指标展示

主表只增加：

- 持有时长；
- 区间位置。

展开 `FundRangeMetrics` 显示：

- 本轮开始日和推导方式；
- 最新官方净值和日期；
- 最高/最低净值和日期；
- 距最高、距最低和区间位置；
- 持仓成本收益率；
- 质量状态和原因。

页面标注“当前值为最近公布的官方净值，不是盘中估值”，并说明分红影响。移动端使用卡片或可展开行，不把全部列压入首屏。

---

## 18. 异常分支

### 18.1 阻止预览

- ZIP 非法、Manifest 缺失或哈希不符；
- 当前持仓文件缺失；
- 当前持仓采集不完整；
- Schema 高于服务器支持版本；
- ZIP 存在路径穿越、符号链接或解压膨胀风险；
- JSON 格式、数量或字段长度超过限制。

### 18.2 可预览但阻止确认

- 当前份额或成本非法；
- 基金代码无法解析；
- 来源账号已绑定另一个 WealthSail 账户；
- 目标账户含其他来源账本事实；
- 当前快照早于最近确认快照；
- 预览后账户事实变化；
- 同一来源账号正在确认其他批次。

### 18.3 可确认但指标降级

- 历史流水不完整；
- 未知业务被排除；
- 无法准确推导开始日；
- 历史净值暂未补齐；
- 基金暂停估值；
- 行情源暂时不可用或发生冲突。

### 18.4 并发与重试

- 相同 ZIP 重复上传返回原批次或完全重复结果；
- 相同幂等键相同内容返回原结果；
- 相同幂等键不同内容返回冲突；
- 同一 binding 确认串行化；
- 两个预览并发时，先确认者成功，后确认者收到预览过期；
- HTTP 超时后客户端复用原幂等键查询稳定结果。

---

## 19. 安全与隐私

- WealthSail 服务端不接触天天基金登录凭据；
- ZIP 不含 Cookie、Token、完整账户号或浏览器存储；
- 临时 ZIP 只在受控目录短暂存在；
- 文件名、日志和错误消息不回显敏感内容；
- 后端不信任扩展名、MIME、Manifest 数量或客户端计算结果；
- 所有数据库写入在受限 API 角色或 Worker 角色下执行；
- 新租户表启用并强制 RLS；
- 绑定、批次、账户和创建人使用同租户复合外键；
- 用户人工修改和每次确认写不可变审计；
- 前端不能访问数据库、service role 或原始 token；
- 真实私有采集数据始终保持 Git 忽略。

---

## 20. 日志、审计与监控

### 20.1 结构化日志

允许记录：

- `request_id`
- `batch_id`
- 处理阶段
- 文件大小
- 包哈希短前缀
- 分类数量
- 标准错误码
- 处理耗时
- 后台任务状态

禁止记录：

- ZIP 正文；
- 完整敏感文件名；
- 持仓金额、收益明细；
- 完整银行卡、账户标识；
- Cookie、Token、密码；
- 原始天天基金响应正文。

### 20.2 审计

审计操作至少包括：

- PACKAGE_PREVIEW_CREATED
- PORTFOLIO_SOURCE_BOUND
- PORTFOLIO_IMPORT_CONFIRMED
- PORTFOLIO_IMPORT_CANCELLED
- HOLDING_START_OVERRIDDEN
- SOURCE_RECORD_EXCLUDED
- POSITION_BASELINE_REVERSED
- POSITION_BASELINE_REPLACED
- POSITION_METRICS_RETRY_REQUESTED

审计保存操作者、批次、绑定、包哈希、版本、计数、人工原因和账本事件引用，不保存不必要的资产明细。

### 20.3 监控指标

- 上传成功/失败数；
- ZIP 安全校验失败数；
- 预览和确认耗时；
- DRAFT 数量和过期数；
- 预览过期冲突数；
- 净值任务积压和重试数；
- 净值回补成功率；
- 指标 READY/PARTIAL/PENDING/CONFLICT 数量；
- 行情源失败率；
- 临时文件清理失败数。

复用现有 Operations JobRuns 和审计页面，不新增独立管理站点。

---

## 21. 数据保留

- 临时 ZIP：解析结束立即删除；
- DRAFT：7 天后自动取消；
- DRAFT 暂存来源记录：取消或过期后清理；
- 已确认来源流水：长期保留；
- 已确认批次和账本事件：长期保留；
- 历史净值：按标的共享并长期缓存；
- 审计：遵循现有项目保留策略；
- 本地 `private-data`：由用户管理，保持 Git 忽略。

---

## 22. TDD 与测试矩阵

### 22.1 实施顺序

1. ZIP 格式和安全校验；
2. 当前持仓和历史流水标准化；
3. 数据库迁移、RLS 和仓储；
4. 上传预览 API；
5. 确认、逐基金基线替换和幂等；
6. 持仓开始日推导；
7. 净值回补和区间指标；
8. 前端导入向导；
9. 持仓指标展示；
10. 端到端、迁移和部署验收。

每个切片先写失败测试，再做最小实现，再重构。不得先完成全部实现后补测试。

### 22.2 采集器测试

- 当前持仓正常、空结果和汇总不一致；
- 多页、末页、重复页和缺页；
- 基金/活期宝分类；
- 真实货币基金不误排除；
- 买入、赎回、分红、再投资、转换和未知业务；
- 状态文案缺失的组合买入、明确失败的组合订单和调仓证据保留；
- 数值日期标准化；
- 脱敏；
- 稳定匿名账号；
- ZIP Manifest 和哈希；
- 输出不含凭据和浏览器会话数据。

### 22.3 ZIP 安全测试

- 正常 v2；
- 缺 Manifest；
- 不支持版本；
- 哈希不符；
- 重复/未声明文件；
- 路径穿越、绝对路径、符号链接；
- 加密/嵌套 ZIP；
- 文件数、压缩/解压大小、压缩率超限；
- 畸形 UTF-8、JSON 深度和长度超限；
- 所有结束路径的临时文件清理。

### 22.4 数据库测试

- 空库完整迁移；
- 从 020 升级到 021；
- Migration readback；
- 旧手工导入兼容；
- RLS 和角色授权；
- 同租户复合外键；
- 来源流水唯一约束；
- 已确认批次和账本事实防篡改；
- 逐基金 ADJUSTMENT/REVERSAL 完整性触发器。

### 22.5 后端测试

- 角色权限和未认证；
- 正常预览不写账本；
- 新增、变化、不变、清仓和冲突比较；
- 旧快照、账号冲突和未知基金；
- 首次确认；
- 二次变化导入；
- 无变化但有新历史流水；
- 空快照清仓；
- 事务失败回滚；
- 幂等重放和幂等冲突；
- 并发确认和预览过期；
- 手工账本冲突；
- 后台任务只创建一次。

### 22.6 计算测试

- 首次买入；
- 多次买入和部分赎回；
- 清仓后重新买入；
- 现金分红和红利再投资；
- 完整和不完整转换；
- 同日清仓买入；
- 部分历史、份额容差和不一致；
- 用户确认开始日；
- 候选日期自动采用、用户覆盖、同账户组合买入、仅调仓、跨账户拒绝和逐基金证据优先；
- 高低点、重复高低日期；
- 当前等于高/低；
- 高低相等；
- 非交易日、暂停估值和异常零净值；
- 行情缺口、来源冲突和算法版本。

### 22.7 前端测试

- 入口和角色权限；
- ZIP 类型、大小、上传和取消；
- 错误码中文映射；
- 刷新恢复 DRAFT；
- 预览页签和数量；
- 阻断状态和确认勾选；
- 防重复点击；
- 409 预览过期；
- 持仓成功/指标失败的正确区分；
- 四种指标状态；
- 缺失值不显示为零；
- 键盘、焦点、屏幕阅读器和移动端布局。

### 22.8 端到端测试

使用构造 ZIP：登录、创建/选择账户、首次预览确认、持仓可见、Worker 回补、指标可见、相同包重放、变化包二次确认、跨租户隔离。

失败路径覆盖非法 ZIP、未知基金、绑定冲突、并发确认、行情失败和空持仓清仓。

---

## 23. 部署和恢复

### 23.1 迁移

新增：

```text
migrations/021_tiantian_fund_import_and_position_metrics.sql
```

迁移必须：

- 添加新表、字段、索引和注释；
- 更新导入类型、项目类型和完整性触发器；
- 配置 RLS 和 API/Worker 授权；
- 保持现有手工导入和历史批次兼容；
- 提供 readback 校验；
- 不删除现有字段或真实数据。

### 23.2 上线顺序

1. 数据库备份并记录 migration；
2. 短暂关闭新写入；
3. 执行 migration 到 021；
4. 执行 migration readback；
5. 部署后端、Worker、前端和新版采集器；
6. 启动 Worker，再重启 API；
7. 执行预览/取消冒烟；
8. 使用构造包确认一次；
9. 检查持仓、任务、审计和日志；
10. 开启生产功能开关。

具体可复制命令在实现完成后同步更新 `docs/10` 和 `docs/11`。

### 23.3 功能开关

```text
WEALTHSAIL_TIANTIAN_IMPORT_ENABLED
```

生产首次部署可先关闭。关闭后停止新上传和确认，但已有持仓、指标和历史记录保持可读。

产生真实导入数据后，不通过删除 021 表结构回滚。故障时关闭开关、回退应用代码或向前修复。

---

## 24. 验收标准

功能只有在以下条件全部满足时完成：

1. 本文所有 P0 功能需求实现；
2. 新旧单元、集成、前端和端到端测试通过；
3. 后端静态检查、前端类型检查/Lint/构建通过；
4. 空库迁移和 020→021 升级通过；
5. Migration readback 通过；
6. 构造 ZIP 完成首次、重复、变化和清仓导入；
7. 真实数据的脱敏副本完成人工验收，真实数据不入 Git；
8. 持仓份额和成本与当前快照一致；
9. 历史流水没有生成虚假 BUY/SELL；
10. 高低点、百分比和质量状态符合本文公式；
11. 日志不泄露凭据、完整账户号和资产明细；
12. 部署步骤、失败恢复和功能开关文档完整；
13. 修改报告写入 `ai-review`；
14. 在 `master` 分支创建详细 Git commit 并成功 push。

---

## 25. 需求—实现—测试追溯摘要

| 能力 | 主要需求 | 主要实现 | 关键测试 |
|---|---|---|---|
| 当前持仓采集 | TT-FR-002/006 | collector holdings adapter | 持仓完整性、汇总差异 |
| 活期宝排除 | TT-FR-004 | classifier + excluded summary | 现金管理排除、货币基金保留 |
| ZIP 安全 | TT-FR-005/007 | imports/package.py | Zip Slip、炸弹、哈希、临时文件 |
| Web 预览 | TT-FR-020~029 | onboarding wizard + preview API | 页签、阻断、刷新恢复 |
| 来源绑定 | TT-FR-040/047 | source_bindings | 多账号、跨租户、冲突 |
| 幂等增量导入 | TT-FR-042~048 | batch/source tx/reversal | 重放、变化、清仓、并发 |
| 持仓周期 | TT-FR-060/061 | source transaction replay | 清仓再买、部分历史、容差 |
| 净值高低点 | TT-FR-062~068 | market observations + metrics | 高低点、缺口、冲突、缓存 |
| 前端展示 | TT-FR-064/065 | HoldingsTable + FundRangeMetrics | READY/PARTIAL/PENDING/CONFLICT |

完整追溯矩阵将在实现时同步更新 `docs/08`。

---

## 26. 实施切片建议

### 切片 A：数据包与采集器

- v2 Schema；
- 当前持仓采集；
- 分类与完整性校验；
- ZIP 生成和安全 parser 单元测试。

### 切片 B：数据库与预览

- migration 021；
- 来源绑定、暂存记录、正式流水和指标投影；
- 上传预览 API；
- 导入历史查询。

### 切片 C：确认和账本

- 逐基金比较；
- REVERSAL + ADJUSTMENT；
- 账户事实哈希；
- 幂等、并发和事务回滚。

### 切片 D：行情与指标

- 历史 NAV 分页回补；
- Worker 持久任务；
- 持仓起点和区间指标；
- 每日更新和失败重试。

### 切片 E：Web UI

- 导入向导；
- 预览异常处理；
- 结果和历史；
- 持仓区间详情。

### 切片 F：全链路验收与部署

- E2E；
- 性能和安全回归；
- docs/08、docs/10、docs/11；
- ai-review 修改报告；
- Git commit 和 push。

---

## 27. 设计确认记录

用户已经逐项确认：

- 方案 A：当前持仓快照为基线；
- 单 ZIP + 预览确认；
- 一个天天基金账号绑定一个 WealthSail 投资账户；
- 最新官方单位净值和本轮持仓区间口径；
- 本地采集 + 服务端统一解析总体方案；
- 数据库与账本设计；
- 后端 API 与异步任务设计；
- 采集器和 ZIP 设计；
- 前端向导与指标展示；
- 计算规则和异常分支；
- TDD、测试和验收设计；
- 部署、监控、恢复和本期边界。

实现开始前，用户需对本规格文档进行最终确认。
