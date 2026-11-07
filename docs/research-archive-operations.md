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

`docker-compose.yml` 的 api、worker、codex-runner 共用后端镜像，三者统一传递 `BUILD_COMMIT` 构建参数。现有 `build_docker_wealthsail.sh` 在构建时自动读取源码目录的完整 Git HEAD，并通过 `sudo env` 传给 Compose。`--check` 保持只读，不要求已有镜像等于当前 HEAD；`--deploy-only` 读取并核对已有镜像，不改写标签。

NAS overlay 中可单独构建同一镜像的 db-init 工具也传递相同参数，避免后续单独构建工具时覆盖提交标签。

直接使用 NAS 包装器时，在 WealthSail 源码目录执行：

```sh
sudo env BUILD_COMMIT="$(git rev-parse --verify HEAD)" \
  "$NAS_TOOLS/nas-compose" ws build api worker codex-runner
```

构建后核对镜像标签，应与本次构建源码的 `git rev-parse HEAD` 一致，不固定为某个旧提交：

```sh
sudo docker image inspect wealthsail-backend:latest \
  --format '{{index .Config.Labels "org.opencontainers.image.revision"}}'
```

未传 Compose 参数时标签为 `unknown`；直接 `docker build` 未传参数时沿用 Dockerfile 的空值。研究验收中的 buildCommit 仅接受完整合法提交号，缺失或 unknown 仍显示未知；代码指纹独立记录。

正式构建/部署脚本执行以下版本门禁：

- 默认模式和 `--build-only` 构建后，镜像 revision 必须等于本次开始时读取的完整 Git HEAD；标签缺失、unknown、镜像缺失或 Git HEAD 中途变化均退出失败。`--build-only` 不要求仍在线的旧容器先升级。
- `--deploy-only` 在启动服务前完成同样核对。所有部署 `up` 均带 `--pull never --no-build`，镜像缺失不会触发隐式构建。
- 部署后的 API、Worker、Runner 每个正式服务实例必须运行，容器 `.Image` 的不可变镜像 ID 必须等于已核对的后端镜像 ID，再按该实际 ID 读取 revision 与 HEAD 对比。每个服务至少有一个正式实例；历史一次性 `run` 容器不充当正式实例。
- 通过健康/认证检查及版本核对后才输出部署完成。操作期间 HEAD 或共享后端镜像标签漂移、容器集合变化均拒绝宣布成功；失败时保留非零退出码，不自动重建或回滚。

这套门禁覆盖 `build_docker_wealthsail.sh` 的构建和部署边界，数据库升级脚本调用它时沿用同样门禁；它不为两次调用之间的迁移过程锁定 Git 或可变镜像标签，期间应避免并发更新源码/构建镜像。前端不在本次后端三版本标签核对范围内，继续执行既有健康检查。

以上示例不代表已构建或部署。可将标签修复合并到下次正式构建；构建镜像不会更新现有运行容器，部署继续按既有流程执行。
