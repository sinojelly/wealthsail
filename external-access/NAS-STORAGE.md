# NAS 持久化目录与存储补充配置

三个 `compose.*.storage.yaml` 是可选补充文件，只调整持久化存储位置。它们不能单独启动服务；原有基础编排、NAS 数据库网络、外网入口、认证隔离及安全参数继续由各自配置负责。完整首次部署见 [DEPLOYMENT.md](./DEPLOYMENT.md)，Codex 登录见 [认证指南](../docs/32.Codex统一模型与NAS认证操作指南.md)。

## 1. 参数与挂载范围

这些参数沿用用户配置中的 `NAS_VALUME_*` 拼写。它们表示持久化数据根目录，不表示允许模型浏览 NAS 上的其他资料。

| 参数及其子目录 | 容器内目标 | 使用方与权限 |
|---|---|---|
| `${NAS_VALUME_WEALTH_SAIL}/avatars` | `/app/backend/static/avatars` | WealthSail API，可写；目录应由 UID/GID `10001:10001` 使用 |
| `${NAS_VALUME_CODEX}/wealthsail/auth` | `/home/appuser/.codex` | 仅 WealthSail Runner 和一次性认证工具，可写以刷新认证；UID/GID `10001:10001`、目录 `0700` |
| `${NAS_VALUME_STUDY_OS}` | `/app/data` | StudyOS 业务数据，可写；按当前 StudyOS 镜像的运行身份设置权限 |
| `${NAS_VALUME_CODEX}/studyos/runtime` | `/root/.codex` | 仅 StudyOS，可写认证和运行状态；当前基础镜像使用 root，目录 `0700` |
| `${NAS_VALUME_CODEX}/studyos/source` | `/run/codex-host` | StudyOS 基础配置保留的只读认证来源；独立于 runtime，可先是空目录 |
| `${NAS_VALUME_CLOUDFLARED}/cloudflared-token` | `/run/secrets/cloudflared_token` | 仅 cloudflared，只读文件 secret；文件 owner `65532:65532`、mode `0400` |

Cloudflared 的父目录由 NAS 管理员保管并设为 `0700`，不把整段目录挂入容器。WealthSail 与 StudyOS 不共用可刷新的认证目录。API、Worker、前端、cloudflared 不挂载 Codex 认证卷。

本补充配置不改变已有 PostgreSQL 服务或其数据卷。WealthSail 的财务数据仍保存在独立 PostgreSQL 数据库，`NAS_VALUME_WEALTH_SAIL` 不是数据库备份或 PG 数据目录。

## 2. 首次启用前的目录检查

先只读检查 NAS 上的每个实际目标：确认绝对路径、解析符号链接后的真实位置、目录 owner/mode、剩余空间，以及目录是否已经承载其他服务。不要将 `/`、系统目录、源码根目录或整个共享盘作为数据目标。创建子目录前确认它仍位于用户指定的数据根目录之内。

首次空目录的 owner/mode 可以按上表准备；目录已存在且有数据时先保留，核对其用途及当前容器映射。不要对已有根目录执行递归 `chown`、递归删除或清空操作。这里的文件只描述挂载，不会替你创建、修改或删除 NAS 目录。

`create_host_path: false` 让 StudyOS 的业务目录缺失时直接报错，避免 Compose 静默创建拼错的宿主目录。两个 Codex local driver bind 目录也必须预先存在。

为 NAS 上的 Compose 单独准备一个 UTF-8、权限 `0600` 的 `external-access/storage.env`，只写下列四个路径参数：

```dotenv
NAS_VALUME_WEALTH_SAIL=/path/to/persistent/wealthsail
NAS_VALUME_STUDY_OS=/path/to/persistent/studyos
NAS_VALUME_CODEX=/path/to/private/codex
NAS_VALUME_CLOUDFLARED=/path/to/private/cloudflared
```

上面全部是占位示例。不要把包含 SSH 密码的本机 `external-access/nas.env` 直接当作 Compose 环境文件，也不要把其内容复制进文档或 Git。`storage.env` 已由仓库的 `*.env` 规则忽略。

StudyOS 自己的 `.env` 还需要设置 `CODEX_HOME_DIR` 为 `${NAS_VALUME_CODEX}/studyos/source` 的实际 NAS 路径。这个路径用于只读来源挂载，运行中的认证写入独立 `studyos/runtime`，不定期用旧来源文件覆盖刷新后的认证。

## 3. 合并文件的顺序

以下命令在 NAS Bash 中使用，先将 `WEALTH_ROOT`、`STUDYOS_DIR` 设为各自源码路径，再设置 `ACCESS_DIR="$WEALTH_ROOT/external-access"`。保持原有 Compose 项目名；原部署若使用 `-p`，每条命令也沿用同一个值。

Cloudflared：

```bash
docker compose --env-file "$ACCESS_DIR/.env" \
  --env-file "$ACCESS_DIR/storage.env" \
  -f "$ACCESS_DIR/compose.yaml" \
  -f "$ACCESS_DIR/compose.cloudflared.storage.yaml" config --quiet
```

WealthSail：

```bash
docker compose --env-file "$WEALTH_ROOT/deployment/nas.env" \
  --env-file "$ACCESS_DIR/.env" \
  --env-file "$ACCESS_DIR/storage.env" \
  -f "$WEALTH_ROOT/docker-compose.yml" \
  -f "$WEALTH_ROOT/docker-compose.nas.yml" \
  -f "$ACCESS_DIR/compose.wealthsail.yaml" \
  -f "$ACCESS_DIR/compose.wealthsail.storage.yaml" config --quiet
```

StudyOS：

```bash
docker compose --env-file "$STUDYOS_DIR/.env" \
  --env-file "$ACCESS_DIR/.env" \
  --env-file "$ACCESS_DIR/storage.env" \
  -f "$STUDYOS_DIR/docker-compose.yml" \
  -f "$ACCESS_DIR/compose.studyos.yaml" \
  -f "$ACCESS_DIR/compose.studyos.storage.yaml" config --quiet
```

用这些完整前缀执行后续 `create`、`run`、`up`、`logs` 等操作。不要只在首次运行时加 storage overlay，更新时又省略。`config --quiet` 通过后，在 NAS 读取合并后的服务挂载信息，确认每个来源和容器目标匹配上表；完整 `config` 会展开其他秘密，不公开其输出。

## 4. 已有命名卷与迁移边界

WealthSail 仍使用 `avatar_data`、`codex_auth` 这两个逻辑卷名，StudyOS 仍使用 `codex-runtime`。文件没有添加固定的全局 `name`，Docker 继续按原 Compose 项目名定位实际卷，避免不同项目共用同一认证卷。

**已有同名命名卷时，不能直接改 `driver_opts` 然后启动。** 先只读检查卷的 driver、options、mountpoint、使用它的容器及已有数据。如果旧卷与新参数不同，停在这里，先备份并制定单独迁移步骤；Docker 不会把原卷中的数据自动搬到新的 NAS 目录。也不能切换项目名来绕开冲突，否则可能得到一套空卷。

StudyOS 原 `./volume` 目录如已有数据库或学习资料，同样先核对并备份，不直接切换到空的 `${NAS_VALUME_STUDY_OS}`。保留旧数据直到新挂载完成隔离恢复验收。

日常维护保留环境文件、项目名和所有持久目录。更新镜像或重新创建容器不需要删除卷。认证卷备份前停止相应应用，备份需限制访问并加密；不要使用 `down -v`、`volume prune` 或清理整个存储根目录来处理启动错误。

## 5. 验证范围

本地可以做 YAML 解析、必要参数和固定子目录检查、Bash 语法及文档链接检查。实际多文件 Compose 合并、宿主目录权限、镜像初始化 named bind volume 的行为和容器读写，必须在 NAS 上验证；本文件不表示这些远程操作已经执行。
