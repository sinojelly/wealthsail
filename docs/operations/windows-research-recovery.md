# Windows 本地研究排队与退出恢复

更新本地测试环境时，先应用迁移 `105_research_queue_control.sql` 和 `106_research_local_exit_evidence.sql`，再重启 API 与研究 Worker。本次代码验证没有迁移现有用户数据库、清除真实模型锁或启动真实研究。

## 排队控制

- OWNER 可在投资机会页面取消本人尚未开始的研究。已开始执行时返回冲突并重新读取状态，不会结束正在运行的模型。
- 排队上限为 5 分钟，执行窗口仍为开始后的 35 分钟。Worker 和页面读取都会持久化排队过期；排队到期、人工取消及维护失效均保留原请求和幂等键。
- 模型槽被占用时显示阻塞原因；采集、等待模型槽、AI 分析、来源核对、报告验证和保存分别展示。过期或取消后必须重新点击，不能自动重新派发。
- 超时不会释放模型锁。最终采集和研究 INSERT 会在锁后再次核对请求状态、时限、当前成员资格与同源批次，防止旧等待者恢复后消耗新执行槽。

## 新版 Windows 退出证明

研究子进程在首次运行前加入私有 Job Object。清理只操作 WealthSail 创建并持有原始身份句柄的进程树，不按进程名扫描或结束全部 `codex.exe`。

退出尚未确认时，Worker 保留有界的私有 Job/进程句柄。恢复服务只重查同一研究身份的内核证据；匹配并确认退出后才 CAS 释放相应锁。存入数据库的安全诊断摘要不构成退出授权。

## 旧版丢失退出证据的受控维护

默认入口只读检查：

```powershell
.\.venv\Scripts\python.exe -m backend.jobs.research_local_recovery --run-id <研究运行ID>
```

它输出该锁的安全状态与快照指纹，不输出 owner token，也不会调用 AI、杀进程或释放锁。

旧锁无法仅靠等待时间、父进程 PID 不存在或重启 Worker 得到退出证明。只有在下列事实成立后，才能使用本机重启维护路径：

1. 维护者能够确认原研究确实在这台 Windows 主机运行；不能从当前配置推断历史执行位置。
2. 这台 Windows 已完整重启，内核启动时间比锁取得时间至少晚 60 秒；休眠、恢复或只重启 Worker 不够。
3. 从锁取得时起系统时间未做调整，内核与数据库当前时钟一致。无法确认时停止维护，继续保留锁。
4. 停止普通研究调度服务，查清其他正在执行的请求和流水线；无关运行中任务、锁竞争或快照变化均会拒绝维护。

具备本地 Worker 数据库权限的维护者，以有效 OWNER 身份和刚检查的精确快照显式执行：

```powershell
.\.venv\Scripts\python.exe -m backend.jobs.research_local_recovery `
  --run-id <研究运行ID> --action legacy-local-reboot `
  --confirm-run <同一研究运行ID> --expect-snapshot <检查返回的指纹> `
  --tenant-id <原租户ID> --owner-user-id <有效OWNER用户ID> `
  --attest-local-origin --attest-no-clock-change
```

该入口仅允许 Windows、明确 loopback 数据库与无远程 Runner 的配置。它把人工本机归属/时钟声明与内核重启证据独立记为 `LOCAL_WINDOWS_REBOOT`，不会伪造模型生命周期的 `exitConfirmed`。

维护在独占派发封锁内完成：保留原运行及报告，只准确终态原请求、旧排队和过期排队，写入持久队列截止与维护审计，再按原 run/token/acquiredAt 快照释放这一把锁。其他请求或流水线无法安全归属时拒绝操作。延迟提交的旧排队也会被截止门禁拒绝，不会在恢复后突然启动。

## 本次验证范围

使用本地冻结样本、不会调用模型的测试替身、安全虚拟子进程和独立的临时 PostgreSQL。真实 PostgreSQL 验证覆盖 OWNER/RLS、取消与认领竞争、锁后超时、维护精确 CAS、表锁权限及持久截止；原用户数据库、NAS、生产环境与真实 Codex 会话均未被维护操作修改。
