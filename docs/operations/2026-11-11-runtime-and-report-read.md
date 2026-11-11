# 2026-11-11 模型失败、账户读取与报告加载排查

本文记录北京时间11月11日晚的生产只读核对及对应代码修复。没有重跑模型、修改认证或部署本次应用修改。

## 今早是哪次执行

| 项目 | 核实结果（北京时间） |
| --- | --- |
| 研究执行 | `65f04383-7c61-41ff-820a-294ef759eb22`，11月11日自动研究 |
| 用量任务 | `eef4105b-8202-41f6-b5a3-83fb503516e9` |
| 模型调用 | `2cbe25d7-6abd-43ab-a9ff-affcf20d8e9e` |
| 派发 | 07:01:26.748 |
| 调用前账户读取 | 约15.1秒，`account/read` 返回错误 |
| 模型观测 | 07:01:42开始；07:04:24收到 `turn.failed`；约167.56秒；未收到完成事件 |
| 调用后账户读取 | 约15.1秒，同样失败 |
| 结果入库 | 07:04:44.940，`FAILED / MODEL_EXECUTION / UNEXPECTED_ERROR / CodexCallError` |

这次是模型执行失败，并非11月10日的行业验收失败，也不是11月6日手动请求的超时。模型未保存可恢复的JSON；审计只有144字节的失败摘要。不能以旧报告已恢复为依据，声称今天的模型推理已经修复或可以重放。

核对时四个应用容器均已是 `114869c97d82edc674410d7e14d5b137e2ed7a50`，没有OOM或重启记录。生产实际已部署上一轮修改；本次症状不能归结为旧镜像。

## 账户与底层日志证据

调用前后账户进程都能启动和完成初始化，随后 `account/read` 约15秒报错。独立的只读RPC复核得到 `error.code=-32603`，脱敏错误摘要为 `workspace routing discovery timed out`，即工作区路由发现超时。它是RPC主动返回错误，区别于应用20秒外层等待期限。

Codex自身的持久日志数据库还保留两条同期错误：07:01:32与07:04:34，模块 `codex_models_manager::manager`，模板为 `list_models{refresh_strategy=online}: failed to refresh available models: request timed out`。这里刷新的是可用模型列表，不能解释为Token过期或Token刷新失败。账户RPC超时与模型目录请求超时都有证据，但不足以证明模型失败与账户失败必然具有同一根因，也不能确定是认证、网络或额度。

晚间在runner内做无认证HEAD连通性复核：已配置代理，`chatgpt.com`没有配置绕过；访问该域名约5秒出现 `ConnectError`，没有HTTP响应。同次 `auth.openai.com` 返回200、`api.openai.com` 返回401（未携带认证，属于正常可达性响应）。这证明“OpenAI API可达”不能代替“ChatGPT链路可达”。它支持继续核对runner经代理到ChatGPT的链路，但当前连通性检查不能反推今早模型的确切错误，也未定位到具体代理规则、TLS或上游故障。本次没有修改代理参数。

旧实现丢弃CLI stderr，`turn.failed`仅留阶段和时间，再被包装成通用 `CodexCallError`。持久日志没有留下这次模型请求的确切错误，因此无法补写一个未经证实的底层原因。

新实现只提取封闭诊断 `version/origin/stage/reasonCode`，及有效的HTTP状态、退出码、RPC码和耗时；经过runner、client、账户缓存、用量持久层和运维接口传递。日志及UI不接收任意stderr、异常正文或模型内容。未知CLI错误明确标为 `CLI_FAILED_UNCLASSIFIED`；明确的工作区路由发现超时为 `WORKSPACE_ROUTING_TIMEOUT`，其他RPC返回超时为 `RPC_RESPONSE_TIMEOUT`，应用外层账户等待超时为 `ACCOUNT_RPC_TIMEOUT`。这些改动不增加重试、模型调用或运行预算，也不改变报告验收规则。

## 11月6日与报告加载

11月6日23:12是真实历史手动请求 `c9ac567a-63e4-4b90-b405-87ffdd81ee10` 的提交时间。页面在等待报告接口时尚未拿到当前流水线身份，旧判断便将该终态请求展开。现在初次读取的历史终态请求先折叠，明确标为历史；本页新提交、正在跟踪或执行中的请求仍按原生命周期展示。不会用浏览器当前时间改写历史时间。

生产浏览器只读重新加载后，已能看到“11月11日07:00 定时自动执行未通过”，历史手动请求折叠；展示报告为11月10日07:07，包含AI硬件方向。这个结果证明身份串线发生于读取等待期间，不代表新代码已经部署。

一次生产报告读取耗时约24.43秒，其中 `context.live_nav_prices` 约19.84秒，数据库池等待为0；另一次读取约16.23秒。已保存报告JSON约14.22MB，含多处重复曲线。没有取得足以证明该请求分钟级SQL死锁的证据。

- 只读报告GET及方向/指数依赖的HTTP截止覆盖响应正文，共30秒；取消请求仍有效，不自动连串重试。8秒后显示读取较慢，超时或离线明确结束等待，可“重新读取报告”；该按钮不会启动研究。
- 已载入内容在后台刷新时继续显示。加载、离线与错误状态采用统一留白和排版，桌面、手机都做浏览器验证。
- 净值历史已完整覆盖请求窗口且近期核查成功时，不能仅因新基金少于120个样本而再次抓取。仍保留全部点与可计算价位；120样本分位约束、尾日落后、未完整覆盖、核查过期等刷新条件不变。
- 新页面对报告读取使用 `compactCharts=true`。仅在API响应副本中省略完全相同且没有前端消费者的重复曲线；提及基金保留其卡片实际使用的内层曲线，其他计划保留外层曲线。所有方向、主题、主题兼容数组、基金、价格及不同时间/来源的曲线都保留，存储与缓存对象不修改；未传该参数的既有接口合同不变。

真正缺少历史时的首次补取仍可能等待外部来源。30秒浏览器截止不会取消服务端已经开始的净值补齐，也不能宣称所有首次读取都已秒开。

## 后续如何定位

部署后在运维“当前Codex账户”和展开的推理明细中查看识别原因、环节以及HTTP/RPC/退出码；推理前后采集各自保留诊断。历史失败不会凭空获得当时没有记录的字段。

以下为NAS已有容器的只读日志命令，不触发研究：

```sh
docker logs --timestamps --since '2026-11-11T07:00:00+08:00' --until '2026-11-11T07:06:00+08:00' wealthsail-worker-1
docker logs --timestamps --since '2026-11-11T07:00:00+08:00' --until '2026-11-11T07:06:00+08:00' wealthsail-codex-runner-1
docker logs --timestamps --since '10m' wealthsail-api-1
```

关注worker的 `daily_research_*` 失败记录、runner的账户阶段及新增 `reason_code/origin/diagnostic_stage/http_status/rpc_code`，以及API的 `perf_stage`、`slow_sql`、`pool_wait_ms`。完整内部日志不要直接公开；定位不需要读取或输出认证文件。

有既有数据库只读运维入口时，可核对指定执行的身份、时间和封闭原因：

```sql
BEGIN READ ONLY;
SELECT r.run_id, r.slot_date, r.dispatched_at,
       t.status, t.recorded_at, t.error_class,
       t.failure_stage, t.failure_reason_code, t.failure_rule_id
FROM research.daily_research_runs AS r
JOIN research.daily_research_results AS t USING (run_id)
WHERE r.run_id = '65f04383-7c61-41ff-820a-294ef759eb22';
SELECT id, task_id, status, started_at, finished_at, error_class,
       details->'researchProgress' AS progress,
       details->'diagnostic' AS diagnostic
FROM research.ai_usage_calls
WHERE id = '2cbe25d7-6abd-43ab-a9ff-affcf20d8e9e';
COMMIT;
```

接口诊断和参考价位修复须部署本次本地提交后生效；账户服务是否恢复、下一次真实模型调用是否成功，仍需独立验证。

## 本地验证结果与限制

后端最终整套为6170通过、478跳过、1失败，另有6条既有python-jose弃用警告。剩余失败是未改动的Windows进程清理测试：将清理期限缩为30毫秒后，进程未在该期限内完成回收；随后整个Windows进程测试文件独立重跑17项通过。没有放宽生产运行或清理预算，也没有把这次整套记录成全绿。新增诊断引入的离线导入冲突已通过将纯元数据模块移到 `backend/common/runtime_diagnostics.py` 修复；原失败所在套件及真实离线隔离回归99项通过，provider导入和socket禁令保持。

前端干净整套1062项通过；后续最终日报/运维接线112项、报告API合同11项目标通过，typecheck、lint、构建通过。后端短历史相关242项、精简响应与原HTTP合同33项通过，Backend Ruff、Python编译和文档检查通过。桌面及手机浏览器夹具验证了慢读、30秒终止、只读重试、离线暂停、历史折叠与诊断排版；这些本地结果不代替部署后的生产连通性和真实模型执行验证。
