# 数据库迁移已提交、最终 readback 失败时的恢复

适用情况：SQL 已提交到迁移账本，API/Worker 因最终校验失败而停止。例如 105、106 已提交，但旧校验器拒绝 Worker 的 `execution_stage`、`stage_updated_at` 更新权限。

先将包含修复的 Git 提交同步到 NAS 源码目录。构建要求源码与本地提交一致；本地提交不会自动推送到远端。保留原始备份 `wealthsail-20261109-002922.dump`，恢复过程不覆盖它。

在 NAS SSH 中进入 WealthSail 源码根目录，确认迁移账本：

```bash
sudo docker exec postgres psql -X -U admin -d wealthsail \
  -c "SELECT version, filename FROM public.wealthsail_schema_migrations WHERE version IN (104,105,106) ORDER BY version;"
```

本次应包含 `104_opportunity_fund_cache.sql`、`105_research_queue_control.sql`、`106_research_local_exit_evidence.sql`。确认 API/Worker 仍停止，然后执行：

```bash
sudo bash upgrade_docker_wealthsail_db.sh --resume-readback
```

该模式检查现有 Compose 配置中的数据库目标、数据库身份及全部迁移文件/账本校验和，要求没有待执行 SQL，且 API/Worker 已停止。它构建已提交源码的镜像，通过现有受保护的管理员凭据启动一次性 `db-init`，先预览，再在迁移锁内执行只读 readback。不会执行初始化安装、迁移 SQL 或账本修复。

日志必须包含每个版本的 `SKIP ... (already applied)`，以及：

```text
SKIP 104_opportunity_fund_cache.sql (already applied)
SKIP 105_research_queue_control.sql (already applied)
SKIP 106_research_local_exit_evidence.sql (already applied)
READBACK OK: ...
```

只有校验成功且迁移账本再次核对通过，脚本才调用 `build_docker_wealthsail.sh --deploy-only`。无需另外提前启动服务或手动执行 `--deploy-only`。失败时保留停止状态，查看具体错误后前向修复；不要修改已执行的 SQL、迁移账本或使用 `--reconcile-drift`。

成功后，用现有 `nas-compose ws ps` 检查运行状态，并验证 HTTPS 登录、数据库相关页面、研究队列操作及 Worker 日志。若部署启动后验证失败，恢复脚本会再次停止 API/Worker；保留日志并检查失败原因，再重新执行恢复模式。

`--check` 和普通 `--apply` 保持原有用途。恢复模式只处理已执行完全部 SQL 的最终校验失败；如果源码还有新 SQL，应先确认为什么恢复所用版本与已执行的版本不一致。
