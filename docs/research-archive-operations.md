# 研究归档容量与保留策略

归档队列与正式材料当前各保留冻结副本。以下工具只检查容量、积压和可考虑回收的候选，不执行清理，不调用模型或消息推送，不增加Worker删除或正文更新权限。

在具有适当读取权限的运维环境配置 `RESEARCH_STORAGE_DATABASE_URL`，再从项目目录运行：

```sh
python -m scripts.research_archive_storage \
  --confirm-target '<host:port/database>' \
  --retention-days 90 --candidate-limit 20 \
  --warn-db-bytes 1073741824 --critical-db-bytes 2147483648 \
  --volume-path '<实际PostgreSQL数据或备份卷挂载路径>'
```

连接必须显式指定，目标须精确匹配host[:port]/database；不自动读取应用生产连接。数据库事务为REPEATABLE READ、READ ONLY，连接及每条查询最多5秒。JSON不输出DSN、冻结正文或原始诊断。需要可读取归档相关表的运维连接，权限不足、缺少迁移或查询失败返回UNKNOWN，不伪装健康。

输出包含四表的总物理大小（含索引/TOAST）、队列状态计数、最老待处理年龄和最多20条冗余候选。默认数据库1 GiB警告、2 GiB严重，可按NAS预算调整；待处理4小时警告、24小时严重，UNRECOVERABLE单独警告。真实卷剩余20%警告、10%严重。退出码0/1/2/3分别表示OK/WARNING/CRITICAL/UNKNOWN，可接NAS计划任务和现有告警系统；本次没有安装计划任务或发送任何告警。

`--volume-path`必须指向执行进程可见的实际NAS卷。应用容器的`/app`通常不能代表PostgreSQL宿主卷；没有提供路径时输出NOT_CHECKED，只检查数据库，不能据此声称NAS磁盘健康。卷无法读取时返回UNKNOWN。检查不计算整个NAS备份集合，备份卷应另设同类容量告警。

保留策略按状态区分：

- AUDIT_PENDING / ARCHIVING：保留全部冻结材料，禁止回收；先排查权限、失败类别、租约和服务存活。
- UNRECOVERABLE：保留人工排障及恢复依据，不能因达到重试次数就清除正文。
- ARCHIVED：默认观察保留90天，最低30天；只有正式材料三份描述的哈希、大小、缺失状态一致且均有正文，INITIAL与冻结验收结果相同，才进入冗余候选。

候选查询先按状态与保留期取最多20条，再做材料一致性核对。它是保守样本，不能当成全量可回收数量；storedBundleBytes是该JSON值的存储大小，不能承诺立即释放等量磁盘。数据库实际空间还受TOAST、MVCC、索引、WAL和备份策略影响。

真正清理应另行实现受控维护流程：先备份与核对只读候选，使用运维专用权限，在持行锁后重新核实ARCHIVED状态、保留期及主副本一致性，仅处理冗余队列正文；保留研究ID、原时间、尝试与错误元数据、材料哈希和维护审计记录，正式材料及INITIAL保持不变。不得清理仍待恢复或不可恢复记录，不得给API/Worker任意DELETE或正文UPDATE权限。本次没有提供执行清理开关，冗余正文仍实际保留。

构建可同时保存完整代码提交号：

```sh
docker build -f backend/Dockerfile --build-arg BUILD_COMMIT="$(git rev-parse HEAD)" -t wealthsail-backend .
```

该示例不代表已构建或部署。未传构建参数时buildCommit为空；代码指纹仍独立记录，不把未知构建号作为验收失败。
