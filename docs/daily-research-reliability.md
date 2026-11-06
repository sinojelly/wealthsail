# 每日研究成本、超时与恢复说明

本次仅本地离线实现与故障注入，不访问 NAS、生产数据库或真实 AI，不推送、不部署。生产故障 `c9ac567a-63e4-4b90-b405-87ffdd81ee10` 的终态和审计材料不改写；既有人工维护流程继续保留。

## 输入实测与业务边界

完整字段报告、Top 20、重复统计及复现命令见[模型输入审计](daily-research-input-audit.md)。同一个本地真实冻结样本的对比如下，不能把另一失败运行的 589635 字节当作这份样本的基线。

| 口径 | 原版字节 | 新版字节 |
| --- | ---: | ---: |
| 完整 Prompt | 592170 | 403055 |
| 实际模型 JSON | 571818 | 390280 |
| Prompt 指令文本 | 20352 | 12775 |
| 独立输出 Schema | 16266 | 16266 |

Prompt 减少 31.94%，不代表 Token 或耗时同比下降。此前成功会话累计输入 1165827 Token、其中缓存输入 918656、输出 22012，不等于单个初始 Prompt 的 Token；这组数据不能证明后续失败由缓存过期导致。

新 Prompt 使用 `daily-research-input-v22` / `daily-model-input-v2`。量化输入保留当前基金身份、价格、日期、口径、完整度、趋势及逐轨候选/入区/阻断/退出状态、申购限制和行业义务，移除服务器已算好的价格几何和展示细节；旧计划仅留同口径比较坐标与风险。重复文本、共同来源元数据和宏观定义采用精确引用，所有被选择的新闻正文和宏观观察点仍可读取。共用 URL 的不同事实不合并。

完整原始 Packet、历史合同、原结果和正式验收 Schema 均保留。默认旧投影 v1 可重现，未知投影版本拒绝。精确 taskId/industryId/kind/query、五类反方核查、10 范围/50 计划查询、来源/日期/风险/行业资格、服务器价格复制与 Decimal 版本比较都不放宽。模型不根据压缩输入重算价格；未审核主题只能作为待审发现/基金观察，不能获得正式资格。64 个主题、8 个正式方向是上限，不是填充目标。

## 分层期限

默认模型执行预算不提高，仍为 900 秒；Pydantic、Runner 请求和期限合同的可配置范围为 30–1200 秒。1500/1800 不属于有效配置。冻结发现预算仍是原版本，其模型执行上限不因清理或 HTTP 余量而提高。

| 层次 | 默认期限/余量 | 含义 |
| --- | ---: | --- |
| CLI 模型 | 900 秒 | 单次实际 CLI 会话预算 |
| 子进程清理 | 最多 15 秒 | 终止、回收和独立后代证明；不是继续推理额度 |
| Runner | 920 秒 | CLI + 清理 15 + 返回余量 5 |
| Worker RPC | 935 秒 | Runner + HTTP/解析余量 15 |
| 退出状态恢复 | 最多 20 秒 | 仅 cancel/status，不启动第二模型 |
| Worker operation | 960 秒 | RPC + 恢复 20 + 余量 5 |
| 账号额度采集 | 单次最多 26 秒，两次共 52 秒 | 在模型 operation 外，不改变业务结果 |
| 手动请求 | 35 分钟 | 原业务总期限，不续期；派发前核对剩余模型/快照及 230 秒后处理余量 |
| 自动流水线 | 默认 7200 秒 | 使用首次领取冻结的绝对期限，重启不按新配置延长 |

`research-workload-v2.modelElapsedScope=MODEL_OPERATION` 表示 Worker 模型操作（含 RPC、执行和必要终止/恢复）的耗时，不含前后账号快照，也不冒称纯 CLI 推理时间。旧 workload-v1 的耗时仍按旧口径显示。实际 CLI/排队/完成时点通过独立生命周期与进度时间区分。业务期限到达后必须进行必要的有界终止及审计收尾，不能把收尾当作新的模型预算。

后处理余量由现有核验边界推导：最多 24 来源、并发 3、每项网络期限 25 秒，最坏网络 200 秒，另留 30 秒解析/持久化余量；不是减少核验或保证任意阻塞数据库都能在该余量内完成。剩余总预算不足则在领取模型槽位前失败，不先消耗模型再赌后处理来得及。

超时来源分别为 `CLI_MODEL_TIMEOUT`、`RUNNER_OPERATION_TIMEOUT`、`WORKER_RPC_TIMEOUT`、`WORKER_OUTER_TIMEOUT`、`RUNNER_DISCONNECTED`；`MODEL_EXIT_UNCONFIRMED` 表示退出仍不能证明。旧 `TIMEOUT` 和旧错误响应继续保守处理，不把无证明的 504 或失联改成确定退出。

## 占用与退出状态机

| 情形 | 证据与动作 |
| --- | --- |
| 领取模型槽位 | 原事务同时提交唯一研究运行与 owner_token；后续失败不重复领取/推理 |
| QUEUED | 只说明 Runner 记录了身份，不证明已创建 CLI |
| RUNNING | 本次调用已开始；读取进度不等于退出证据 |
| TERMINATING | 已请求终止；不能据此释放占用 |
| EXITED 且 reaped/descendantsConfirmed/exitConfirmed 全为真 | 校验 run/owner/call 绑定后，仅更新对应 guard.released_at；研究结果可仍为 FAILED/UNCERTAIN |
| UNKNOWN、断连、Runner 重启、旧身份、证明缺失 | 保留占用及受控人工流程；不凭时钟、租约或单次进程扫描解锁 |
| Worker 可证明 operation 从未进入 | 没有 CLI/RPC 派发，可释放未使用占用；不将其伪装成模型已退出 |

调用身份含严格 UUID `runId/ownerToken/callId`，callId 从持久 run/owner 确定性派生，Worker 重启后可重构而无需新增数据库字段。对外诊断不输出 ownerToken 或身份绑定摘要。旧随机调用身份无法重构时保持未知。

Linux 的专用监督进程仅拥有本次 CLI，设置 subreaper、独立 CLI 会话、受限 TERM/KILL 清理，回收自身后代直到 `waitpid` 返回 ECHILD，才经 CLI 不继承的匿名控制 FD 返回证明。PID/starttime/pidfd 避免复用误杀；没有可信证明或内核能力则保持未知。它不回收 Runner 的其他账号/私人调用进程。父 Runner 消失时仍有本地执行期限和父退出终止请求。创建进程期间的取消也不能凭“没拿到句柄”证明没有派发。

Runner 的 status/cancel 接口复用内部 Bearer 鉴权并严格匹配三项身份，只返回有界状态。取消应答不等于退出确认。内存注册表与派发墓碑有界；详情过期/Runner 重启后查询未知，持久跨重启防重复派发仍由 Worker 原数据库成本屏障保证。

立即恢复通道结束后，独立 Worker 服务启动即检查、之后每 60 秒最多读取 4 个未释放占用；只查询同身份 status，匹配肯定证明才 CAS 释放原 guard，不取消、不推理、不改研究终态。旧 UNCERTAIN 记录整行不变。Runner 的证明若消失则保留人工流程，本地 CLI 跨 Worker 重启同样不猜测退出。

## 搜索、进度与 Token

本轮默认仍只启动一个模型会话。50 个冻结计划查询不是工具硬限额；外部 CLI 可以继续搜索/打开网页，不能宣称有预执行硬拦截。Prompt 要求同轮精确任务只执行一次、复用已提供全文及同原文阅读、先做真实高价值事件和已冻结范围，动态全市场基金检索由服务器完成。

原 receipt-v2 的真实已完成 JSONL 执行匹配、run/asOf/manifest/validatedAt 边界和五类核查放行不变。独立统计区分计划、匹配、实际搜索查询、打开、网页内搜索、重复精确 query 和重复原始 URL 打开；事件重放不重复计数。同 URL 不能证明同一经济事实，统计或哈希不能替代执行凭证。缺失操作数、未知事件及覆盖不完整保持未知。不同日期的 NOT_FOUND 不继承，没有做跨日“无风险”缓存。

安全进度仅包含协议版本、枚举、数字及时间：实际开始/最后事件、累计观察时间、工具动作/活动工具、搜索/open/find 数和受信阶段。progress.elapsedMs 是最近一次 CLI 事件观测口径，静默时不伪装成实时推理秒表；lifecycle.elapsedMs 从 Runner 注册起计时，包含排队/清理，不能替代纯模型耗时。`agent_message` 不证明正在生成最终报告，也不能可靠统计模型在正文反复引用来源的次数。进度和 Token 不证明进程已退出。

Runner 状态采样约 15 秒，Worker 写进度至少间隔 10 秒、单次写最多 1 秒，失败不改变研究业务结果。复用既有 AI usage details JSONB、call/task/run 绑定和 RLS，不新增迁移；只更新 RUNNING 且未完成的本次调用，拒绝倒退/错配进度，终态合并保留最后安全快照。已有权限下的 AI 用量面板沿用约 30 秒只读轮询；没有绕 RLS 给普通共享首页增加私人/全局调用进度。

取得的输入/缓存/输出 Token 保留真实值；缺失仍为 UNKNOWN，不用 0 填补。正常结果先于独立 after-account 采集保留，账号快照失败不能把已完成模型变成重新推理的许可。未完成 JSON 不进入正式验收；保留上一有效正式报告和独立确定性扫描，不伪装今日研究成功。

## 资源与运行观测

代码显示研究与机会 NAV 后台任务共用 Worker、连接池和网络出口，不能据此断言它造成了已报告的 NAS 超时。只让新增的可选机会 NAV 暖缓存在有未终结模型运行时延后下一批，优先级观察窗由最大 Worker 模型操作预算加账号预算推导为 1312 秒；旧 UNCERTAIN 或没有终态的过窗孤儿占用不永久饿死暖缓存。过窗只影响可选采集调度，绝不表示模型退出或允许解锁。持仓/自选等关键价格采集不关闭，旧净值不冒充新鲜。

版本库 Compose 文件未声明 CPU、内存或交换区限额，不能由此推断 NAS 实际容器没有限制。本次没有生产 CPU、内存、交换、磁盘、代理排队指标；启动任务重叠仍是待验证假设。

观察 `daily_research_workload`、`daily_research_model_exit`、各超时 reason、`research_exit_recovery` 及既有 archive/cache/NAV duration 日志。只记录安全计数/枚举，不记录完整 Prompt、网页正文、凭证、任意模型输出或 owner。平台用量面板与研究验收工作量提供受权查询。

## 验证与提交

本地三批提交：输入审计与瘦身 `0ab360e`；期限与可信退出 `0c842a3`；搜索、进度及资源优先级随本说明第三批提交。全过程为零真实 AI 调用，没有新增迁移。

最终验证使用仓库虚拟环境，pytest 禁用缓存并将临时目录放在忽略的 artifacts 内。结果如下；专项与全套有交集，不将次数相加宣称独立覆盖数量。

| 范围 | 结果 |
| --- | --- |
| 后端 `tests/unit` 全套 | 5520 passed，363 skipped，6 条既有 python-jose datetime.utcnow 弃用警告 |
| 前端全套，2 workers | 78 文件，799 passed |
| 隔离本机 PostgreSQL 18.1，migration 1–104 | 46 passed：缓存、NAV、进度绑定/RLS、退出恢复和召回 |
| 其余 integration/e2e，本机隔离数据库且关闭 live E2E | 33 passed，5 skipped；不运行真实服务/消息或 NAS 安装 |
| 第二批独立故障回归 | 304 passed、3 Linux-only skipped，另 3 条实库恢复通过 |
| Worker 进度接线及相关回归 | 80 passed；最后新增 pending cancel ack 的 Worker 用例亦通过 |
| 最后取消状态补丁及 Worker/client/用量/快照定向复验 | 99 passed |
| 前端静态与产物 | typecheck、ESLint（零警告）、生产 build 全通过 |

全套之后仅补充取消应答的状态展示与对应故障回归，并执行上述 99 项定向复验；没有改变退出释放条件。363 个单测跳过项包括未配置的专用数据库/权限环境以及 3 个真实 Linux 进程案例，不能作为通过证据。

### 故障覆盖与证明边界

下表“通过”均指本地协议/数据库或故障注入验证，真实外部 CLI/网络不在本轮范围。Linux 真进程案例单独列为待验证。

| 场景 | 本地覆盖与结果 |
| --- | --- |
| 模型按时返回 | Worker 正常返回、单 usage 调用、匹配退出释放；无退出证明则仍保留 guard |
| CLI 超时并确认退出 | `test_confirmed_cli_timeout_releases_only_its_guard_without_reinference`，失败终态保留、只释放原 owner |
| Runner 504 | client server-timeout 与 Runner CLI/operation timeout 注入；旧响应缺证明保持未知，不重试模型 |
| Worker HTTP 先超时 | RPC/outer origin 分开；cancel/status 恢复，未确认不释放 |
| 客户端断开 | 真实 ASGI disconnect 消息、已运行/排队取消；join 前不宣称未派发 |
| Runner 崩溃 | 协议失联保持未知；监督进程父 SIGKILL 的真实 Linux 案例已交付但本机跳过 |
| Runner 重启 | 新注册表无旧证明；Worker 持久成本屏障禁止重复消耗槽位 |
| 拒绝 TERM、存在后代 | supervisor OS 注入要求全部 waitpid 回收；真实 setsid/stubborn 子进程与父 SIGKILL 案例待 Linux 实跑 |
| 创建子进程时取消/OS spawn 卡住 | 等句柄有界、UNKNOWN、迟到句柄托管清理；不能以尚无句柄证明未派发 |
| 模型完成、账号快照失败/中断 | 原输出、实测 Token 与退出证明保留；模型耗时排除快照 |
| 输出完成、报告验证失败 | 冻结原始材料，不放行来源/风险/价格，不重新推理；退出释放独立于业务资格 |
| 退出证明迟到、Worker 重启 | 确定性身份重构、后台 status 恢复；实库检查原 UNCERTAIN 结果整行不变 |
| 错运行/旧 owner/重复回执交错 | 三项绑定与原 owner CAS；实库/并发测试只释放一次，外来证明不生效 |

可在未来隔离 Linux 环境执行以下零 AI 验证；必须实际通过，内核能力缺失导致的 skip 不满足进程回收验收：

```sh
python -m pytest tests/unit/test_research_supervisor.py tests/unit/test_research_supervisor_linux.py tests/unit/test_codex_research.py -q -p no:cacheprovider
```

## NAS 灰度步骤与回滚条件

本节为以后受控灰度准备，不代表已经部署。

1. 备份数据库、审计材料、镜像版本和受保护配置，保留故障研究原状；不要把认证文件或个人参数提交 Git。本次无需新迁移。
2. 在隔离 Linux 容器先运行上一节命令，使用 fixture Python 子进程、无网络，验证拒绝 TERM、脱离会话、Runner 父退出和回收失败均符合证明边界；不先消耗真实 AI。
3. 先更新 Runner，再更新 Worker/API/前端，确保新身份字段、status/cancel 和安全观测协议同时可用；混合旧 Runner 拒绝新请求时不得自动降级重发。
4. 保持默认 CLI 900 秒及原总预算，受权选一次受控研究，核对只有一个模型调用。记录 Prompt/模型操作/请求与清理时长、真实 Token、实际工具动作、可信来源/主题、原始正式方向与严格合格计划数。
5. 故障演练确认肯定退出后释放、未确认保留、迟到证明恢复、旧终态不改；只读机会和确定性扫描可继续展示，不能以更少风控或更多虚构正式方向充成功率。
6. 比较同口径超时率、退出确认率和人工解锁次数；不承诺从 15 分钟缩到 2–3 分钟。

出现跨运行解锁、未证明后代退出却释放、重复派发/额度消费、来源/价格/行业风控回退、日志泄漏、进度写入影响模型结果或无法解释的资源放大，应停止新研究派发并回退整套镜像。先确认正在执行的进程状态，保留占用与审计，不能为了回滚清空 guard 或改旧终态。不同版本 Prompt/投影不需要重写历史冻结材料。

## 尚需外部验证的风险

本机 Windows 没有可用的本地 Linux Engine，真实 Linux 后代回收测试未执行；纯协议、OS 故障注入和 Windows 子进程测试不能替代该项。Windows 未配置 Job Object，不能证明全部后代退出时保守保持未知。Linux 内核 subreaper/pidfd 能力、容器信号/权限、外部 CLI JSONL 实际事件形态、代理失联、供应商限流/延迟和实际缓存 Token 行为均须实机核对。Runner 崩溃导致的状态遗失可能继续需要人工恢复；没有以单次 `/proc` 扫描或容器存活替代证明。
