# 机会推荐 NAS 部署检查

本页是 migration 104 的升级与验收步骤，不表示已在真实 NAS 执行。完整构建、备份、管理员迁移和重启方法沿用 [NAS 独立 PostgreSQL 部署手册第 8 节](31.NAS独立PostgreSQL部署与验证操作手册.md#8-备份与后续更新)。不把数据库口令、认证文件或个人部署参数写入 Git。

## 扩展与增量迁移

`pg_trgm` 已由 migration **068** 的 `CREATE EXTENSION IF NOT EXISTS pg_trgm` 安装；104 复用其 `gin_trgm_ops` 创建指数名称 GIN 索引。已成功完整执行 068 的同一数据库通常不需要再次安装扩展。迁移历史从别处导入、扩展被移除或数据库未执行完整升级时，须先核对实际状态。

管理员在目标数据库检查扩展：

```sql
SELECT extname, extversion FROM pg_extension WHERE extname = 'pg_trgm';
```

如无结果，由具备该数据库扩展安装权限的管理员执行 `CREATE EXTENSION IF NOT EXISTS pg_trgm;`，并确认 PostgreSQL 镜像已提供该扩展。API/Worker 的受限业务账号不承担扩展安装或 DDL。不要修改已应用的 068 来绕过权限，也不要删除迁移账本重跑。

按既有部署流程先备份、构建镜像，再在无运行中任务的维护窗口停止 API/Worker。`build_docker_wealthsail.sh` 不自动执行迁移；用原有管理员环境调用 `scripts/migrate.py`，保持显式目标校验：

```bash
python scripts/migrate.py --environment production --confirm-target '<host[:port]/database>' --dry-run
python scripts/migrate.py --environment production --confirm-target '<host[:port]/database>'
```

以上为参数模板，管理员 DSN 通过既有受保护环境提供；NAS 容器部署应使用部署手册的 `db-init --entrypoint python` 包装和现有网络 overlay。预览需核对目标及 `PLAN`/`SKIP`；实际执行应看到 104 的 `APPLIED`（已应用则为 `SKIP`）和最终 `READBACK OK`。readback 校验缓存的强制 RLS、成员可见性、API 只读列授权、Worker 权限、快照边界触发器、目录修订触发器及两个 trigram 索引。任何 checksum 或 readback 错误均须先处理，再恢复运行服务。

## 冷启动与缓存状态

Worker 的归档服务在启动时立即执行首轮同步，之后每轮结束等待 60 秒。即使禁用新的 AI 研究，该恢复服务也会启动；只有 API、没有 Worker 的部署不会自动补建。已有 `ARCHIVED`、已有主题快照但没有候选缓存的研究，无需重跑模型或等待新的经济归档：同一服务会自动发现并补建。

每次领取至多为 8 个缺失或失效快照创建候选任务，再领取一个到期任务；每轮补建至多 4 次。新归档还会直接尝试自己的缓存。补建排序以原研究 `validated_at` 降序为准，旧周期即使延迟归档或更早排队也不会挤在最新周期之前；未来才到重试时间的任务仍遵守退避。实际等待时间还包括本轮经济归档、已领取工作及匹配耗时，60 秒是轮间等待，并非就绪时限。

管理员或 Worker 可只读检查指定研究的缓存状态，运行时把参数绑定为所验收的 `run_id`：

```sql
SELECT q.run_id, q.status AS archive_status,
       c.status AS cache_status, c.validated_at, c.attempts,
       c.next_attempt_at, c.last_error_class, c.updated_at,
       c.catalog_day, c.catalog_revision, v.revision AS current_catalog_revision
FROM research.research_validation_archive_queue q
LEFT JOIN research.opportunity_fund_cache c ON c.run_id = q.run_id
CROSS JOIN reference.opportunity_fund_catalog_revision v
WHERE q.run_id = $1;
```

`ARCHIVED` 且 `cache_status` 为空表示尚未补建，`PENDING`/`COMPUTING` 表示正在等待或执行，`FAILED` 表示本版本已耗尽 8 次尝试。只查看安全错误类与时钟，不导出租约 token、冻结正文或私有材料。缓存还须匹配算法版本、目录生效日、修订号和快照摘要；单独看到旧的 `READY` 不代表当前读取必定命中。同一版本与目录条件下的 `FAILED` 不会自动再次领取；仅修复代码中的非版本错误也不会重置它。须先定位原因，再制定恢复处理；修正版匹配版本或正常目录修订／日期失效会走现有重建路径。当前没有手动恢复按钮或 API，不通过 GET 触发同步搜索，不直接清空候选状态或覆盖历史档案。

页面只从数据库读取候选、当前身份和申购状态、最终展示基金的本地净值。缺失时返回 `FUND_RECALL_PENDING`，重试耗尽返回 `FUND_RECALL_FAILED`。前端对候选 pending 每 3 秒检查一次、至多 10 次，这只是页面轮询预算；超过预算可稍后手动检查，不代表后台停止。独立 NAV 后台服务启动即扫描，之后每轮结束等待 60 秒，从最多 4 轮最新有效 `READY` 缓存发现目标并同步，仍按原验收时钟优先，不让归档循环等待供应商网络；候选 `READY` 与净值历史齐备是两个独立状态。候选已就绪但 NAV 仍待同步时不新增自动轮询，后台落库后需用页面的手动重新检查入口刷新读取；后台完成不会让已展示卡片立即变价。

NAV 每个研究周期取每主题当前展示的前三组产品，至多 216 个本地候选；最多 4 轮跨周期按代码统一去重。逐批复用本地可信历史、跳过仍在租约或冷却期的代码，实际领取供应商采集的代码全局至多 24 个，已复用或冷却中的产品不占用这 24 个名额，后续主题与周期可以继续取得进度。每批最多 4 路并发采集，整批网络预算 240 秒，按代码持有 360 秒数据库时钟租约。请求目标为 550 个样本，读侧仍按至少 120 样本、来源证明、连续性及新鲜度判断可用性。已取得但尚不可用的历史记录为 `PARTIAL`，不会当作成功进入 12 小时冷却；`PARTIAL` 或失败冷却 1 小时。持久记录仅存公开基金代码，不关联用户、租户或研究主题。实际供应商返回数量与质量可能仍不足，需读回确认。

## 耗时与实机验收

本地合成样本的匹配 SQL、完整候选发现和 NAS 实际端到端耗时应分别记录：

| 观测对象 | 应包含的工作 | 验收方式 |
| --- | --- | --- |
| 匹配 SQL | 主数据／指数检索与身份关联 | 在隔离库用同规模样本记录 `EXPLAIN (ANALYZE, BUFFERS)`；不要把单条 SQL 时间当成页面就绪时间 |
| 经济归档 | 领取归档任务、冻结经济档案持久化 | 查看 `research_archive_completed` / `research_archive_failed` 的 `run_id` 和 `duration_ms`；该阶段不含候选计算 |
| 完整候选缓存 | 领取候选任务、候选发现、缓存写入 | 查看 `opportunity_cache_completed` 的实际 `run_id`、`status` 和 `duration_ms`，异常查看 `opportunity_cache_pending`；对照缓存 `updated_at` |
| NAV 同步 | 供应商网络、限流／退避、本地历史落库 | 查看 `opportunity_nav_complete` 的 selected／reused／claimed／persisted／usable／partial／failed 计数和 `duration_ms`；selected 是本地候选池大小，可能大于 24，claimed 才是至多 24 的本轮供应商预算。异常查看 `opportunity_nav_failed` 的安全错误类与 `duration_ms`。persisted 不等于 usable，须再读回本地历史；网络耗时不属于纯本地发现 SQL |
| 页面 GET | 可见性检查、候选命中、当前动态事实、本地 NAV、响应传输 | 在登录态检查请求耗时与 pending 状态，分别记录冷启动和命中缓存的结果 |

迁移 readback 成功后重启 Worker/API，先用已经归档的最新可见研究观察后台补建，再验收页面：候选应来自该轮主题、纯负面／宏观主题分流应保留，缺少可信 NAV 时仍应显示待同步，不能编造价格或买入资格。实机数据规模、NAS CPU／磁盘、并发归档和 NAV 网络均会改变耗时；已有本地约 0.6 秒 SQL／0.9 秒发现样本不能推导 NAS 必然在 2–3 秒内完成全链路。保留脱敏阶段日志作为实际结果，未在 NAS 执行的项目应标为待验证。
